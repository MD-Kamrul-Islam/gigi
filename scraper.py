"""
Crawls every website listed under `sources:` in config.yaml and saves each
page to pages.jsonl (one JSON object per line).

Nothing about which sites to crawl lives in this file -- add or remove a
source in config.yaml and re-run. Each page carries its source `label`
("College of Business", "Career Services"), which follows the text all the
way through to the answer, so Gigi can tell students which office a page
came from.

Guardrails, per source:
- stays on `allowed_domain`, optionally within `path_prefix`
- never enters a path under `blocked_prefixes`
- skips non-HTML files
- hard `max_pages` cap
- polite delay between requests
"""

import json
import time
from collections import deque
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from settings import cfg, sources

OUTPUT_FILE = "pages.jsonl"

SKIP_EXTENSIONS = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip",
                   ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".mp4")


def normalize_url(url: str) -> str:
    """One canonical spelling per page, so nothing is crawled twice."""
    url = url.split("#")[0].strip()
    if url.endswith("/") and urlparse(url).path != "/":
        url = url.rstrip("/")
    return url


def should_skip(url: str) -> bool:
    return any(url.lower().split("?")[0].endswith(ext) for ext in SKIP_EXTENSIONS)


def is_allowed(url: str, source: dict) -> bool:
    """True if this URL belongs to the source and isn't in a blocked section."""
    parsed = urlparse(url)
    if parsed.netloc != source["allowed_domain"]:
        return False
    path = parsed.path
    prefix = source.get("path_prefix") or ""
    if prefix and not path.startswith(prefix):
        return False
    for blocked in source.get("blocked_prefixes") or []:
        if path == blocked or path.startswith(blocked):
            return False
    return True


def clean_text(soup: BeautifulSoup) -> str:
    """Page text with site chrome removed. The chunker strips whatever
    repeated boilerplate survives this pass."""
    for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                     "noscript", "form"]):
        tag.decompose()
    for selector in ["#menu", ".menu", ".navbar", ".breadcrumb", ".skip-link",
                     "#skip-to-content"]:
        for tag in soup.select(selector):
            tag.decompose()

    # Prefer the main content region when the page marks one.
    main = soup.find("main") or soup.find(id="main") or soup.find("article")
    root = main if main is not None else soup

    text = root.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_links(soup: BeautifulSoup, base_url: str, source: dict) -> list[str]:
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        full_url = normalize_url(urljoin(base_url, href))
        if is_allowed(full_url, source) and not should_skip(full_url):
            links.append(full_url)
    return links


def crawl_source(source: dict, session: requests.Session) -> list[dict]:
    """Crawl one configured website. Returns its pages."""
    label = source.get("label", source["allowed_domain"])
    max_pages = int(source.get("max_pages", 300))
    delay = float(cfg("crawl", "request_delay_seconds", 0.5))
    timeout = int(cfg("crawl", "timeout_seconds", 10))
    min_chars = int(cfg("crawl", "min_page_chars", 50))

    print(f"\n--- Crawling: {label} ({source['allowed_domain']}, max {max_pages}) ---")

    start = normalize_url(source["start_url"])
    queue = deque([start])
    queued = {start}
    visited = set()
    pages = []

    while queue and len(visited) < max_pages:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code != 200:
                continue
            if "text/html" not in resp.headers.get("Content-Type", ""):
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            text = clean_text(soup)
            title_tag = soup.find("title")
            title = title_tag.get_text().strip() if title_tag else url

            if len(text) > min_chars:
                pages.append({"url": url, "title": title, "text": text,
                              "source": label})
                print(f"[{label}] [{len(pages)}] {url}")

            for link in extract_links(soup, url, source):
                if link not in visited and link not in queued:
                    queued.add(link)
                    queue.append(link)

        except requests.RequestException as e:
            print(f"  skipped (error): {url} -- {e}")

        time.sleep(delay)

    print(f"--- {label}: {len(pages)} pages ---")
    if queue:
        print(f"    NOTE: hit max_pages with {len(queue)} URLs unvisited. "
              f"Raise max_pages for this source in config.yaml if needed.")
    return pages


def crawl():
    """Crawl every configured source into one pages.jsonl."""
    configured = sources()
    if not configured:
        print("No sources configured in config.yaml -- nothing to crawl.")
        return []

    session = requests.Session()
    session.headers.update(
        {"User-Agent": "GigiChatbot-Pilot/1.0 (ISU College of Business)"})

    all_pages = []
    for source in configured:
        try:
            all_pages.extend(crawl_source(source, session))
        except Exception as e:
            # One broken source must not lose the pages already crawled.
            print(f"[error] source '{source.get('label')}' failed: {e}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for page in all_pages:
            f.write(json.dumps(page, ensure_ascii=False) + "\n")

    by_source = {}
    for p in all_pages:
        by_source[p["source"]] = by_source.get(p["source"], 0) + 1
    print(f"\nDone. {len(all_pages)} pages total -> {OUTPUT_FILE}")
    for label, count in by_source.items():
        print(f"  {label}: {count}")
    return all_pages


if __name__ == "__main__":
    crawl()
