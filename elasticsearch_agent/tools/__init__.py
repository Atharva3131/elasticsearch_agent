"""Tool registry — returns every LangChain tool the ReAct agent can call."""
from __future__ import annotations

from elasticsearch_agent.tools.index_search_tool import create_search_tool
from elasticsearch_agent.tools.index_count_tool import create_count_tool
from elasticsearch_agent.tools.index_data_tool import IndexShowDataTool
from elasticsearch_agent.tools.index_details_tool import IndexDetailsTool
from elasticsearch_agent.tools.list_indices_tool import ListIndicesTool
from elasticsearch_agent.tools.chart_tool import create_chart_tool


def get_all_tools() -> list:
    return [
        create_search_tool(),
        create_count_tool(),
        IndexShowDataTool(),
        IndexDetailsTool(),
        ListIndicesTool(),
        create_chart_tool(),
    ]
