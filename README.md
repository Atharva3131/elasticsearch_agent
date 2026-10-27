# Elasticsearch AI Agent POC

A command-line Elasticsearch agent that accepts natural-language requests, executes Elasticsearch searches or aggregations, and returns formatted results or chart artifacts.

See [POC.md](POC.md) for the complete project walkthrough, chart example, audit-log format, and console tool-calling trail.

## What it demonstrates

- Natural-language queries against Elasticsearch indexes.
- Search, count, index inspection, and index listing tools.
- Deterministic pie charts generated from Elasticsearch aggregation buckets.
- Exact-value aggregations using mapped keyword fields such as `status.keyword`.
- Customer-data-safe result formatting.
- Structured request and tool-call auditing in `artifacts/audit.jsonl`.
- Console visibility for `request.received`, `tool.started`, `tool.completed`, `tool.failed`, and request completion events.

## Requirements

- Python 3.11+
- Elasticsearch 8.x
- Elasticsearch credentials and a reachable Elasticsearch server

## Installation

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[test]"
```

### Linux/macOS

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

## Configuration

Create a `.env` file with values appropriate for your environment:

```dotenv
ELASTIC_SERVER=https://127.0.0.1:9200
ELASTIC_USER=elastic
ELASTIC_PASSWORD=<elasticsearch-password>
ELASTIC_VERIFY_CERTIFICATES=false
ELASTIC_INDEX=order
CHART_GROUP_FIELD=marketplaceName.keyword
APP_TIMEZONE=Asia/Kolkata
AUDIT_LOG_PATH=artifacts/audit.jsonl
CHART_OUTPUT_DIR=artifacts/charts
```

`CHART_GROUP_FIELD` must be a mapped keyword or other aggregatable field. For the order-status chart, the workflow uses `status.keyword` because `status` is mapped as text.

## Usage

### One-shot query

```powershell
python -m elasticsearch_agent.cli "Count the records in the order index"
```

### Pie chart

```powershell
python -m elasticsearch_agent.cli "Query the order index and create a pie chart showing the count and percentage of orders grouped by status. Use a terms aggregation on the status field across all records. Render and return the pie chart with slice labels, percentages, legend, title, and total order count."
```

The generated chart is written to `artifacts/charts/`. Status charts use a descriptive filename such as `order_status_distribution_pie.png`.

### Interactive chat

```powershell
python -m elasticsearch_agent.cli --chat
```

Enter one request at a time. Type `exit` or `quit` to leave. For long PowerShell prompts, one-shot mode is usually easiest because the entire quoted prompt is passed as one argument.

### Health check

```powershell
python -m elasticsearch_agent.cli --health-check
```

## Audit logging

Each request receives a unique request ID. Audit events are appended as JSON Lines to `artifacts/audit.jsonl` and mirrored to stderr, so the same tool-calling trail is visible in the console.

Typical successful chart flow:

```text
request.received
  → tool.started: elastic_index_search_tool
  → tool.completed: elastic_index_search_tool
  → tool.started: create_pie_chart
  → tool.completed: create_pie_chart
  → request.completed
```

Audit records include timestamps, sequence numbers, tool arguments, durations, status, result metadata, errors, and chart artifact paths. Credentials, API keys, passwords, tokens, and URL credentials are redacted.

## Output artifacts

```text
artifacts/
├── audit.jsonl
└── charts/
    └── order_status_distribution_pie.png
```

## Tests

```powershell
pytest -q
```

Tests mock Elasticsearch and model providers, so they do not require production credentials or a live Elasticsearch server.
