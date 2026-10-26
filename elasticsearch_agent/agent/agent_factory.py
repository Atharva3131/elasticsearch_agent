"""Compatibility entrypoint exposing the LangGraph workflow."""
from elasticsearch_agent.agent.workflow import build_graph, run_question

__all__ = ["build_graph", "run_question"]
