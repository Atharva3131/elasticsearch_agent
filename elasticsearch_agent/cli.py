from __future__ import annotations

import argparse

from elasticsearch_agent.config import cfg
from elasticsearch_agent.agent.workflow import run_question


def main() -> int:
    parser = argparse.ArgumentParser(description="Elasticsearch AI agent POC")
    parser.add_argument("question", nargs="?", help="natural-language question")
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
    if not args.question:
        parser.error("provide a question or --health-check")
    try:
        print(run_question(args.question))
        return 0
    except Exception as exc:
        print(f"Request failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
