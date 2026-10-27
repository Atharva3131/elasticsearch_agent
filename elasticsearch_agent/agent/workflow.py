"""Explicit LangGraph workflow for database questions and chart requests."""
from __future__ import annotations

import calendar
import ast
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from elasticsearch_agent.config import cfg, validate_field_name
from elasticsearch_agent.log_init import AuditLogger
from elasticsearch_agent.tools.chart_tool import create_pie_chart


def is_chart_request(text: str) -> bool:
    return bool(re.search(r"\b(chart|pie chart|graph|plot)\b", text, re.I))


def month_range(now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now(ZoneInfo(cfg.timezone))
    last = calendar.monthrange(now.year, now.month)[1]
    return f"{now.year:04d}-{now.month:02d}-01", f"{now.year:04d}-{now.month:02d}-{last:02d}"


def requested_month_range(text: str) -> tuple[str, str] | None:
    """Return the requested month when the user included one, otherwise None."""
    match = re.search(
        r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(20\d{2})\b",
        text,
        re.I,
    )
    if not match:
        return None
    month = list(calendar.month_name).index(match.group(1).capitalize())
    year = int(match.group(2))
    last = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last:02d}"


def extract_index(text: str) -> str | None:
    # Prefer the explicit "X index" form, then support natural phrasing such
    # as "from the order index" without capturing the article "the".
    match = re.search(r"\b([A-Za-z0-9_.-]+)\s+index\b", text, re.I)
    if not match:
        match = re.search(r"\bfrom\s+(?:the\s+)?[`']?([A-Za-z0-9_.-]+)", text, re.I)
    return match.group(1) if match else cfg.default_index


def revenue_query(index: str, text: str) -> dict:
    group_field = validate_field_name(cfg.chart_group_field)
    requested_range = requested_month_range(text)
    if requested_range:
        start, end = requested_range
        return {"size": 0, "query": {"range": {"orderDate": {"gte": start, "lte": end}}}, "aggs": {"by_category": {"terms": {"field": group_field}, "aggs": {"revenue": {"sum": {"field": "orderTotal"}}}}}}
    # Do not assume the server's current month contains data. Select the latest
    # month with documents after Elasticsearch returns the monthly buckets.
    return {"size": 0, "aggs": {"by_month": {"date_histogram": {"field": "orderDate", "calendar_interval": "month", "min_doc_count": 1}, "aggs": {"by_category": {"terms": {"field": group_field}, "aggs": {"revenue": {"sum": {"field": "orderTotal"}}}}}}}}


def status_query(index: str) -> dict:
    """Build the aggregation used for an order-status pie chart."""
    # `status` is mapped as text in the order index; aggregations must use its
    # exact-value keyword subfield.
    return {"size": 0, "aggs": {"by_status": {"terms": {"field": "status.keyword"}}}}


def chart_query(index: str, text: str) -> dict:
    if re.search(r"\bstatus(?:es)?\b", text, re.I):
        return status_query(index)
    return revenue_query(index, text)


def chart_title(text: str) -> str:
    if re.search(r"\bstatus(?:es)?\b", text, re.I):
        return "Order Status Distribution"
    return "Order Revenue Distribution"


def buckets_from_result(result: str | dict) -> tuple[list[str], list[float]]:
    data = json.loads(result) if isinstance(result, str) else result
    aggs = data.get("aggregations", data)
    if "by_month" in aggs:
        months = aggs["by_month"].get("buckets", [])
        if not months:
            raise ValueError("no dated documents were found for the chart")
        aggs = months[-1].get("by_category", {})
    elif "by_status" in aggs:
        aggs = aggs["by_status"]
    elif "buckets" not in aggs:
        # Aggregation names are user/query-defined. Select the first bucket
        # aggregation when the response is not one of the legacy shapes.
        aggs = next((value for value in aggs.values() if isinstance(value, dict) and "buckets" in value), {})
    if "buckets" not in aggs:
        raise ValueError("chart requires a bucket aggregation; the Elasticsearch result had none")
    buckets = aggs["buckets"]
    return [str(b["key"]) for b in buckets], [float(b.get("revenue", {}).get("value", b.get("doc_count", 0))) for b in buckets]


def format_search_result(result: str | dict) -> str:
    """Turn the search tool's raw response into a concise, safe CLI summary."""
    data = ast.literal_eval(result) if isinstance(result, str) else result
    total = data.get("total", 0)
    total_value = total.get("value", total) if isinstance(total, dict) else total
    total_label = f"at least {total_value}" if isinstance(total, dict) and total.get("relation") == "gte" else str(total_value)
    hits = data.get("hits", [])
    lines = [f"Found {total_label} matching records. Showing {len(hits)} records:"]
    for hit in hits:
        source = hit.get("_source", {})
        fields = [
            f"id={hit.get('_id', '')}",
            f"orderNo={source.get('orderNo', '')}",
            f"orderDate={source.get('orderDate', '')}",
            f"marketplace={source.get('marketplaceName', '')}",
            f"status={source.get('status', '')}",
            f"total={source.get('orderTotal', '')}",
        ]
        lines.append("- " + ", ".join(fields))
    return "\n".join(lines)


def build_graph():
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    class State(TypedDict, total=False):
        question: str
        chart: bool
        index: str
        query: dict
        result: object
        chart_result: dict
        answer: str
        audit: AuditLogger

    def interpret(state: State):
        state["chart"] = is_chart_request(state["question"])
        state["index"] = extract_index(state["question"]) or ""
        if state["chart"]:
            if not state["index"]:
                raise ValueError("Please specify an Elasticsearch index for the chart request")
            state["query"] = chart_query(state["index"], state["question"])
        else:
            state["query"] = {"query": {"match_all": {}}}
        return state

    def query(state: State):
        # Keep the workflow's pure helpers importable for offline tests. The
        # legacy tool module has optional LangChain dependencies and is only
        # needed when a real Elasticsearch query is executed.
        from elasticsearch_agent.tools.index_search_tool import elastic_search

        if not state.get("index"):
            raise ValueError("No Elasticsearch index configured; set ELASTIC_INDEX or specify one in the request")
        # Aggregation queries must use size=0 so the search helper returns the
        # aggregation payload rather than a page of document hits.
        state["result"] = state["audit"].timed_tool("elastic_index_search_tool", {"index_name": state["index"], "query": state["query"]}, lambda: elastic_search(state["index"], json.dumps(state["query"]), 0, 0 if state.get("chart") else 10))
        return state

    def chart(state: State):
        labels, values = buckets_from_result(state["result"])
        title = chart_title(state["question"])
        state["chart_result"] = state["audit"].timed_tool("create_pie_chart", {"title": title, "labels": labels, "values": values}, lambda: create_pie_chart(title, labels, values))
        return state

    def final(state: State):
        state["answer"] = (f"Created the chart from Elasticsearch aggregation data: {state['chart_result']['artifact_path']}" if state.get("chart") else format_search_result(state.get("result", "{}")))
        state["audit"].event("request.completed", status="success", final_output=state["answer"], chart_artifact=state.get("chart_result", {}).get("artifact_path"))
        return state

    graph = StateGraph(State)
    graph.add_node("interpret", interpret)
    graph.add_node("query", query)
    graph.add_node("chart", chart)
    graph.add_node("final", final)
    graph.add_edge(START, "interpret")
    graph.add_edge("interpret", "query")
    graph.add_conditional_edges("query", lambda s: "chart" if s.get("chart") else "final", {"chart": "chart", "final": "final"})
    graph.add_edge("chart", "final")
    graph.add_edge("final", END)
    return graph.compile()


def run_question(question: str) -> str:
    audit = AuditLogger(cfg.audit_log_path)
    audit.start(question)
    try:
        return build_graph().invoke({"question": question, "audit": audit})["answer"]
    except Exception as exc:
        audit.event("request.failed", status="failure", error=repr(exc))
        raise
