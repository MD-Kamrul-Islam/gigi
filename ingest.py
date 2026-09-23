"""
Builds Gigi's knowledge base: scrape -> load manual docs -> chunk -> embed.

Run this whenever you change config.yaml's sources, add manual documents, or
want fresh content from the websites. It's the only build command you need.

Usage: python ingest.py
"""

from catalog_csv_loader import load_catalog_csvs
from chunker import build_chunks
from corrections import build_correction_pages
from manual_docs import load_manual_docs
from scraper import crawl
from settings import sources
from vectorstore import build_index, reset_index_cache


def run():
    labels = ", ".join(s.get("label", "?") for s in sources())
    print(f"=== Step 1/6: crawling configured sources ({labels}) ===")
    crawl()

    print("\n=== Step 2/6: loading manual documents ===")
    load_manual_docs()

    print("\n=== Step 3/6: loading verified corrections ===")
    build_correction_pages()

    print("\n=== Step 4/6: loading catalog CSV data ===")
    load_catalog_csvs()

    print("\n=== Step 5/6: chunking ===")
    build_chunks()

    print("\n=== Step 6/6: embedding (this calls the OpenAI API) ===")
    build_index()
    reset_index_cache()

    print('\nDone. Try: python chat_cli.py "what student organizations can I join?"')


if __name__ == "__main__":
    run()
