"""LangGraph ReAct agent — the LLM decides which tools to call and why."""
from __future__ import annotations

import json
from typing import Annotated

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from elasticsearch_agent.config import cfg
from elasticsearch_agent.log_init import AuditLogger
from elasticsearch_agent.prompts import SYSTEM_MESSAGE
from elasticsearch_agent.tools import get_all_tools


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    audit: AuditLogger


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def _agent_node(state: AgentState) -> AgentState:
    """Call the LLM with all tools bound. Returns the next AI message."""
    tools = get_all_tools()
    llm = cfg.llm().bind_tools(tools)
    response: AIMessage = llm.invoke(state["messages"])
    return {"messages": [response]}


def _tools_node(state: AgentState) -> AgentState:
    """Execute every tool call the LLM requested, capturing its reason field."""
    audit: AuditLogger = state["audit"]
    tools = {t.name: t for t in get_all_tools()}

    last_message: AIMessage = state["messages"][-1]

    # Option 1: extract any reasoning text the LLM produced before the tool calls.
    # Models often emit a short sentence before calling tools — use it as a shared
    # fallback when a specific tool call doesn't include a reason field.
    llm_preamble: str = (last_message.content or "").strip() if isinstance(last_message.content, str) else ""

    results: list[BaseMessage] = []

    for tool_call in last_message.tool_calls:
        tool_name: str = tool_call["name"]
        args: dict = dict(tool_call["args"])  # copy so we don't mutate the message

        # Pull the reason the LLM supplied in the tool arguments.
        reason: str = (args.pop("reason", "") or "").strip()

        # Option 1: fall back to the LLM's preamble text if no per-tool reason.
        if not reason:
            reason = llm_preamble

        # Option 3: if still empty, synthesize a readable reason from tool name + key args.
        if not reason:
            reason = _synthesize_reason(tool_name, args)

        tool = tools.get(tool_name)
        if tool is None:
            output = f"Error: unknown tool '{tool_name}'"
            audit.event("tool.failed", tool=tool_name, status="failure", error=output)
        else:
            output = audit.timed_tool(
                name=tool_name,
                args=args,
                fn=lambda t=tool, a=args: t.invoke(a),
                reason=reason,
            )

        results.append(
            ToolMessage(
                content=str(output),
                tool_call_id=tool_call["id"],
            )
        )

    return {"messages": results}


def _synthesize_reason(tool_name: str, args: dict) -> str:
    """Option 3: build a readable reason from tool name and its key arguments."""
    _TOOL_TEMPLATES = {
        "elastic_index_search_tool": lambda a: f"Searching index '{a.get('index_name', '?')}' with an Elasticsearch query.",
        "elastic_index_count_tool": lambda a: f"Counting records in index '{a.get('index_name', '?')}'.",
        "elastic_index_show_data": lambda a: f"Fetching sample data from index '{a.get('index_name', '?')}'.",
        "elastic_index_show_details": lambda a: f"Inspecting mappings and settings of index '{a.get('index_name', '?')}'.",
        "elastic_list_indices": lambda a: "Listing all available Elasticsearch indices.",
        "create_pie_chart": lambda a: f"Rendering pie chart '{a.get('title', '?')}' from aggregation data.",
    }
    template = _TOOL_TEMPLATES.get(tool_name)
    return template(args) if template else f"Calling {tool_name}."


def _should_continue(state: AgentState) -> str:
    """Route back to the agent after tool calls, or end when the LLM is done."""
    last: AIMessage = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("agent", _agent_node)
    graph.add_node("tools", _tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


# ---------------------------------------------------------------------------
# Public entry point (used by cli.py)
# ---------------------------------------------------------------------------

def run_question(question: str) -> str:
    audit = AuditLogger(cfg.audit_log_path)
    audit.start(question)

    from langchain_core.messages import SystemMessage
    initial_messages = [
        SystemMessage(content=SYSTEM_MESSAGE),
        HumanMessage(content=question),
    ]

    try:
        final_state = build_graph().invoke(
            {"messages": initial_messages, "audit": audit}
        )
        # The last message is the LLM's final answer.
        answer: str = final_state["messages"][-1].content
        audit.event(
            "request.completed",
            status="success",
            final_output=answer,
        )
        return answer
    except Exception as exc:
        audit.event("request.failed", status="failure", error=repr(exc))
        raise


def run_question_with_callback(question: str, on_event) -> str:
    """Like run_question but fires on_event(record) for every audit event.
    Used by the Streamlit UI to stream tool progress in real time.
    """
    audit = AuditLogger(cfg.audit_log_path, on_event=on_event)
    audit.start(question)

    from langchain_core.messages import SystemMessage
    initial_messages = [
        SystemMessage(content=SYSTEM_MESSAGE),
        HumanMessage(content=question),
    ]

    try:
        final_state = build_graph().invoke(
            {"messages": initial_messages, "audit": audit}
        )
        answer: str = final_state["messages"][-1].content or ""
        # Guard against empty responses — surface as an error rather than silent blank.
        if not answer.strip():
            raise ValueError("The model returned an empty response. Please try again.")
        audit.event("request.completed", status="success", final_output=answer)
        return answer
    except Exception as exc:
        audit.event("request.failed", status="failure", error=repr(exc))
        raise
