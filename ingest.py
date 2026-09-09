"""
Builds Gigi's knowledge base: scrape -> load manual docs -> chunk -> embed.

Run this whenever you change config.yaml's sources, add manual documents, or
want fresh content from the websites. It's the only build command you need.

Usage: python ingest.py
"""

from chunker import build_chunks
from manual_docs import load_manual_docs
from scraper import crawl
from settings import sources
from vectorstore import build_index, reset_index_cache


def run():
    labels = ", ".join(s.get("label", "?") for s in sources())
    print(f"=== Step 1/4: crawling configured sources ({labels}) ===")
    crawl()

    print("\n=== Step 2/4: loading manual documents ===")
    load_manual_docs()

    print("\n=== Step 3/4: chunking ===")
    build_chunks()

    print("\n=== Step 4/4: embedding (this calls the OpenAI API) ===")
    build_index()
    reset_index_cache()

    print('\nDone. Try: python chat_cli.py "what student organizations can I join?"')


if __name__ == "__main__":
    run()
