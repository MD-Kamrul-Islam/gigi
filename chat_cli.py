"""
Talk to Gigi in the terminal -- full answers with personality, no browser.

Usage:
  python chat_cli.py "what student organizations can I join?"
  python chat_cli.py                  (interactive, with follow-up memory)
"""

import sys

from gigi_chat import answer_question


def show(result: dict):
    print(f"\nGigi: {result['answer']}\n")
    print(f"  confidence: {result['confidence']}")
    if result.get("search_query"):
        print(f"  searched:   {result['search_query']}")
    for s in result["sources"]:
        print(f"  source [{s['score']:.3f}] ({s['source']}): {s['url']}")
    print()


def main():
    if len(sys.argv) > 1:
        show(answer_question(" ".join(sys.argv[1:])))
        return

    print("Chat with Gigi (empty line or Ctrl+C to quit)\n")
    history = []
    while True:
        try:
            q = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print(); break
        if not q:
            break
        result = answer_question(q, history=history)
        show(result)
        history += [{"role": "user", "content": q},
                    {"role": "assistant", "content": result["answer"]}]


if __name__ == "__main__":
    main()
