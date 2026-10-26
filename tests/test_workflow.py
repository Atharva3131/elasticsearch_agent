from pathlib import Path

from elasticsearch_agent.agent.workflow import buckets_from_result, is_chart_request, month_range
from elasticsearch_agent.tools.chart_tool import create_pie_chart


def test_chart_intent_and_month_range():
    assert is_chart_request("Create a pie chart for revenue")
    assert month_range()[0].count("-") == 2


def test_aggregation_buckets_are_chart_inputs():
    labels, values = buckets_from_result({"aggregations": {"buckets": [{"key": "A", "doc_count": 2}]}})
    assert labels == ["A"] and values == [2.0]


def test_chart_tool_creates_png(tmp_path: Path):
    result = create_pie_chart("Revenue", ["A", "B"], [2, 3], str(tmp_path / "chart.png"))
    assert Path(result["artifact_path"]).exists()


def test_chart_tool_rejects_mismatched_data(tmp_path: Path):
    try:
        create_pie_chart("Revenue", ["A"], [1, 2], str(tmp_path / "chart.png"))
    except ValueError as exc:
        assert "equal lengths" in str(exc)
    else:
        raise AssertionError("expected validation failure")
