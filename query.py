"""
Retrieval tester: shows the raw chunks the search finds, with no LLM involved.
Use this to diagnose "why didn't Gigi find X" -- if the right page doesn't
appear here, it's a content or retrieval problem, not a personality one.

Usage: python query.py "what are some RSOs at ISU?"
"""

import sys

from vectorstore import search


def main():
    if len(sys.argv) < 2:
        print('Usage: python query.py "your question here"')
        sys.exit(1)

    question = " ".join(sys.argv[1:])
    results = search(question, top_k=5)

    print(f'\nQuestion: "{question}"\n')
    for i, r in enumerate(results, 1):
        print(f"{i}. [{r['score']:.3f}] {r['title']}  ({r['source']})")
        print(f"   {r['url']}  chunk #{r['chunk_index']}")
        print(f"   \"{r['text'][:200].replace(chr(10), ' ')}...\"\n")


if __name__ == "__main__":
    main()
