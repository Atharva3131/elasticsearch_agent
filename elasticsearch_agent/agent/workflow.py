"""Explicit LangGraph workflow for database questions and chart requests."""
from __future__ import annotations

import calendar
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from elasticsearch_agent.config import cfg
from elasticsearch_agent.log_init import AuditLogger
from elasticsearch_agent.tools.chart_tool import create_pie_chart
from elasticsearch_agent.tools.index_search_tool import elastic_search


def is_chart_request(text: str) -> bool:
    return bool(re.search(r"\b(chart|pie chart|graph|plot)\b", text, re.I))


def month_range(now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now(ZoneInfo(cfg.timezone))
    last = calendar.monthrange(now.year, now.month)[1]
    return f"{now.year:04d}-{now.month:02d}-01", f"{now.year:04d}-{now.month:02d}-{last:02d}"


def extract_index(text: str) -> str | None:
    match = re.search(r"(?:index|from)\s+[`']?([A-Za-z0-9_.-]+)", text, re.I)
    return match.group(1) if match else cfg.default_index


def revenue_query(index: str, text: str) -> dict:
    if not cfg.chart_group_field:
        raise ValueError("Set CHART_GROUP_FIELD to the mapped categorical field before requesting a pie chart")
    start, end = month_range()
    return {"size": 0, "query": {"range": {"orderDate": {"gte": start, "lte": end}}}, "aggs": {"by_category": {"terms": {"field": cfg.chart_group_field}, "aggs": {"revenue": {"sum": {"field": "orderTotal"}}}}}}


def buckets_from_result(result: str | dict) -> tuple[list[str], list[float]]:
    data = json.loads(result) if isinstance(result, str) else result
    aggs = data.get("aggregations", data)
    if "buckets" not in aggs:
        raise ValueError("chart requires a bucket aggregation; the Elasticsearch result had none")
    buckets = aggs["buckets"]
    return [str(b["key"]) for b in buckets], [float(b.get("revenue", {}).get("value", b.get("doc_count", 0))) for b in buckets]


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
            state["query"] = revenue_query(state["index"], state["question"])
        else:
            state["query"] = {"query": {"match_all": {}}}
        return state

    def query(state: State):
        if not state.get("index"):
            raise ValueError("No Elasticsearch index configured; set ELASTIC_INDEX or specify one in the request")
        state["result"] = state["audit"].timed_tool("elastic_index_search_tool", {"index_name": state["index"], "query": state["query"]}, lambda: elastic_search(state["index"], json.dumps(state["query"]), 0, 10))
        return state

    def chart(state: State):
        labels, values = buckets_from_result(state["result"])
        state["chart_result"] = state["audit"].timed_tool("create_pie_chart", {"title": state["question"], "labels": labels, "values": values}, lambda: create_pie_chart(state["question"], labels, values))
        return state

    def final(state: State):
        state["answer"] = (f"Created the chart from Elasticsearch aggregation data: {state['chart_result']['artifact_path']}" if state.get("chart") else str(state.get("result", "No result")))
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
        audit.event("request.completed", status="failure", error=repr(exc))
        raise
