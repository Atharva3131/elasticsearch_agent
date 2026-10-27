from __future__ import annotations

import argparse
from collections.abc import Callable

from elasticsearch_agent.config import cfg
from elasticsearch_agent.agent.workflow import run_question


def chat_loop(
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> int:
    """Run an interactive session until the user asks to leave."""
    output_fn("Interactive chat started. Type 'exit' or 'quit' to leave.")
    while True:
        try:
            question = input_fn("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("\nGoodbye!")
            return 0

        if not question:
            continue
        if question.casefold() in {"exit", "quit"}:
            output_fn("Goodbye!")
            return 0

        try:
            output_fn(f"agent> {run_question(question)}")
        except Exception as exc:
            # Keep the session alive when one request fails.
            output_fn(f"Request failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Elasticsearch AI agent POC")
    parser.add_argument("question", nargs="?", help="natural-language question (one-shot mode)")
    parser.add_argument(
        "--chat",
        action="store_true",
        help="start an interactive chat session",
    )
    parser.add_argument("--health-check", action="store_true", help="ping Elasticsearch")
    args = parser.parse_args()
    print("Elasticsearch AI agent POC starting")
    if args.health_check:
        if not cfg.elastic_server:
            print("Elasticsearch configuration missing: ELASTIC_SERVER")
            return 2
        try:
            print("Elasticsearch reachable:", cfg.elasticsearch().ping())
            return 0
        except Exception as exc:
            print("Elasticsearch unreachable:", exc)
            return 3
    if args.chat or not args.question:
        return chat_loop()

    try:
        print(run_question(args.question))
        return 0
    except Exception as exc:
        print(f"Request failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
