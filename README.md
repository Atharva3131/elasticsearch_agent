# Elasticsearch AI Agent POC

This POC uses an explicit LangGraph workflow. Existing Elasticsearch tools remain reusable, while chart requests follow a deterministic sequence:

`request -> interpret -> Elasticsearch aggregation -> pie-chart tool -> final response`

The chart tool is called exactly once after a successful Elasticsearch result. Chart values are taken from aggregation buckets; no data is fabricated.

## Requirements and setup

Python 3.11 or newer is required.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Required environment variables depend on the selected provider:

```dotenv
ELASTIC_SERVER=https://127.0.0.1:9200
ELASTIC_USER=elastic
ELASTIC_PASSWORD=<elasticsearch-password>
ELASTIC_VERIFY_CERTIFICATES=false
ELASTIC_INDEX=<known-index-name>
CHART_GROUP_FIELD=<mapped-categorical-field>
APP_TIMEZONE=Asia/Kolkata
LLM_PROVIDER=sarvam
SARVAM_API_KEY=<sarvam-api-key>
SARVAM_MODEL=sarvam-m
```

OpenAI fallback:

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=<openai-api-key>
OPENAI_MODEL=gpt-4o-mini
```

Optional paths are `AUDIT_LOG_PATH` (default `artifacts/audit.jsonl`) and `CHART_OUTPUT_DIR` (default `artifacts/charts`). The LLM client is lazy, so imports and local tests do not require credentials or a live Elasticsearch server.

## Run

```bash
python -m elasticsearch_agent.cli "Count the records in the orders index"
python -m elasticsearch_agent.cli "Create a pie chart for this month's revenue from orders"
python -m elasticsearch_agent.cli --health-check
```

The health check is optional and is the only command intended to verify live Elasticsearch connectivity. Audit events are written as JSON Lines to `artifacts/audit.jsonl` and mirrored to stderr. PNG charts are written to `artifacts/charts`.

## Tests

```bash
pytest -q
```

Tests should mock Elasticsearch and model providers; they do not require external credentials. The current chart POC assumes revenue is represented by `orderTotal` and dates by `orderDate`. Live mapping inspection is still required before production use. Requests without an explicit or configured index cannot be answered safely.
