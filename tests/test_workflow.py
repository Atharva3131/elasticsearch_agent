from pathlib import Path
from types import SimpleNamespace

from elasticsearch_agent.agent.workflow import buckets_from_result, chart_query, chart_title, extract_index, format_search_result, is_chart_request, month_range, requested_month_range, revenue_query
from elasticsearch_agent.tools.chart_tool import create_pie_chart


def test_chart_intent_and_month_range():
    assert is_chart_request("Create a pie chart for revenue")
    assert month_range()[0].count("-") == 2


def test_extract_index_handles_articles_and_explicit_index_phrasing():
    assert extract_index("Show records from the order index") == "order"
    assert extract_index("Show records from order") == "order"


def test_aggregation_buckets_are_chart_inputs():
    labels, values = buckets_from_result({"aggregations": {"buckets": [{"key": "A", "doc_count": 2}]}})
    assert labels == ["A"] and values == [2.0]


def test_status_chart_query_groups_orders_by_status():
    query = chart_query("order", "Create a pie chart grouped by status")
    assert query == {"size": 0, "aggs": {"by_status": {"terms": {"field": "status.keyword"}}}}
    assert buckets_from_result('{"by_status":{"buckets":[{"key":"completed","doc_count":8}]}}') == (["completed"], [8.0])
    assert chart_title("Create a pie chart grouped by status") == "Order Status Distribution"


def test_explicit_month_is_used_for_chart_query():
    assert requested_month_range("chart revenue for March 2022") == ("2022-03-01", "2022-03-31")


def test_chart_without_month_uses_latest_month_buckets():
    result = {"aggregations": {"by_month": {"buckets": [
        {"key_as_string": "2022-01-01", "by_category": {"buckets": [{"key": "A", "revenue": {"value": 2}}]}},
        {"key_as_string": "2022-03-01", "by_category": {"buckets": [{"key": "B", "revenue": {"value": 3}}]}},
    ]}}}
    assert buckets_from_result(result) == (["B"], [3.0])


def test_search_result_is_human_readable_and_excludes_sensitive_fields():
    result = format_search_result({"total": {"value": 1}, "hits": [{
        "_id": "1",
        "_source": {
            "orderNo": "A-1", "orderDate": "2022-03-01", "marketplaceName": "Demo",
            "status": "completed", "orderTotal": 12.5, "customerInfo": {"email": "hidden@example.com"},
        },
    }]})
    assert "Found 1 matching records" in result
    assert "orderNo=A-1" in result
    assert "hidden@example.com" not in result


def test_chart_tool_creates_png(tmp_path: Path):
    result = create_pie_chart("Revenue", ["A", "B"], [2, 3], str(tmp_path / "chart.png"))
    assert Path(result["artifact_path"]).exists()


def test_chart_tool_uses_descriptive_default_filename(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("elasticsearch_agent.tools.chart_tool.cfg", SimpleNamespace(chart_output_dir=tmp_path))
    result = create_pie_chart("Order Status Distribution", ["completed"], [2])
    assert Path(result["artifact_path"]).name == "order_status_distribution_pie.png"


def test_chart_tool_rejects_mismatched_data(tmp_path: Path):
    try:
        create_pie_chart("Revenue", ["A"], [1, 2], str(tmp_path / "chart.png"))
    except ValueError as exc:
        assert "equal lengths" in str(exc)
    else:
        raise AssertionError("expected validation failure")
