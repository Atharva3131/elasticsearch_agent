import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from elasticsearch_agent import config
from elasticsearch_agent.log_init import AuditLogger
from elasticsearch_agent.tools import index_search_tool


def test_settings_creates_one_cached_client_without_logging_credentials(monkeypatch):
    client = object()
    constructor = Mock(return_value=client)
    monkeypatch.setattr("elasticsearch.Elasticsearch", constructor)
    settings = config.Settings(
        elastic_server="https://user:password@example.test:9243",
        elastic_user=None,
        elastic_password=None,
    )

    assert settings.elasticsearch() is client
    assert settings.elasticsearch() is client
    constructor.assert_called_once_with(
        "https://user:password@example.test:9243",
        basic_auth=None,
        verify_certs=True,
    )


def test_search_uses_configured_client(monkeypatch):
    client = SimpleNamespace(search=Mock(return_value={"hits": {"hits": [{"_id": "1"}]}}))
    monkeypatch.setattr(config.Settings, "_elasticsearch_client", property(lambda self: client))

    result = index_search_tool.elastic_search("orders", json.dumps({"query": {"match_all": {}}}), 0, 5)

    assert "_id" in result
    client.search.assert_called_once()
    assert client.search.call_args.kwargs["index"] == "orders"


def test_aggregation_result_is_valid_json(monkeypatch):
    client = SimpleNamespace(search=Mock(return_value={"aggregations": {"by_status": {"buckets": []}}}))
    monkeypatch.setattr(config.Settings, "_elasticsearch_client", property(lambda self: client))

    result = index_search_tool.elastic_search("orders", json.dumps({"size": 0, "aggs": {"by_status": {"terms": {"field": "status"}}}}), 0, 0)

    assert json.loads(result)["by_status"]["buckets"] == []


def test_search_client_exception_is_propagated(monkeypatch):
    client = SimpleNamespace(search=Mock(side_effect=ConnectionError("connection refused")))
    monkeypatch.setattr(config.Settings, "_elasticsearch_client", property(lambda self: client))

    with pytest.raises(ConnectionError, match="connection refused"):
        index_search_tool.elastic_search("orders", '{"query": {"match_all": {}}}', 0, 5)


def test_audit_records_tool_and_request_failures(tmp_path: Path):
    audit = AuditLogger(tmp_path / "audit.jsonl")
    audit.start("Count records in orders")

    with pytest.raises(RuntimeError, match="network failure"):
        audit.timed_tool("elastic_index_search_tool", {"index_name": "orders"}, lambda: (_ for _ in ()).throw(RuntimeError("network failure")))
    audit.event("request.failed", status="failure", error="network failure")

    records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    failed_tool = next(record for record in records if record["event"] == "tool.failed")
    failed_request = next(record for record in records if record["event"] == "request.failed")
    assert failed_tool["status"] == "failure"
    assert failed_tool["duration_ms"] >= 0
    assert failed_tool["error_type"] == "RuntimeError"
    assert failed_request["status"] == "failure"
