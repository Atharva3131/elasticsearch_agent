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
from elasticsearch_agent.auth import verify_token, AuthError, ROLE_ADMIN
from elasticsearch_agent.tools.index_search_tool import set_user_context
from elasticsearch_agent.policy_guard import classify_intent


# ---------------------------------------------------------------------------
# Graph state
# ---------------------------------------------------------------------------

class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    audit: AuditLogger
    claims: dict  # JWT claims (sub, role, seller_id, etc.)
    _guardrail_denied: bool  # Flag set by guard node if request is denied


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def _guard_node(state: AgentState) -> AgentState:
    """
    Policy guard: use LLM-as-judge to classify user intent against guardrail policy.
    If intent violates policy, return a denial message instead of invoking the agent.
    """
    audit: AuditLogger = state["audit"]
    claims: dict = state.get("claims", {})
    role: str = claims.get("role", "")
    
    # Skip guard for ROLE_ADMIN (no restrictions)
    if role == ROLE_ADMIN:
        return state
    
    # Extract the user's question from the first HumanMessage
    user_question = ""
    for msg in state["messages"]:
        if isinstance(msg, HumanMessage):
            user_question = msg.content
            break
    
    if not user_question:
        return state
    
    # Use LLM to classify intent against policy
    llm_classifier = cfg.llm()  # Get LLM without tools
    try:
        is_allowed, reasoning = classify_intent(user_question, role, llm_classifier)
        
        if is_allowed:
            # Request is compliant; continue to agent
            audit.event(
                "request.guardrail_check",
                status="allowed",
                role=role,
                reason=reasoning or "Request complies with policy."
            )
            return state
        else:
            # Request violates policy; deny immediately
            violation_reason = reasoning or "Unspecified policy violation."
            audit.event(
                "request.denied",
                status="denied",
                role=role,
                violation=violation_reason
            )
            # Return an AI message with the denial and mark as denied
            denial_message = f"Access denied: {violation_reason}"
            state["messages"].append(AIMessage(content=denial_message))
            state["_guardrail_denied"] = True
            return state
    
    except Exception as exc:
        # If guard evaluation fails, log and allow the request to proceed
        # (fail-open: security checks should not block legitimate access on error)
        audit.event(
            "request.guardrail_error",
            status="error",
            error=repr(exc),
            reason="Guard evaluation failed; request allowed to proceed."
        )
        return state


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


def _after_guard(state: AgentState) -> str:
    """Route to agent if allowed, or directly to END if guardrail denied."""
    if state.get("_guardrail_denied"):
        return END
    return "agent"


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
    graph.add_node("guard", _guard_node)
    graph.add_node("agent", _agent_node)
    graph.add_node("tools", _tools_node)
    graph.add_edge(START, "guard")
    # After guard: go to agent if allowed, END immediately if denied
    graph.add_conditional_edges("guard", _after_guard, {"agent": "agent", END: END})
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


# ---------------------------------------------------------------------------
# Public entry point (used by cli.py)
# ---------------------------------------------------------------------------

def run_question(question: str, token: str | None = None) -> str:
    # --- Auth enforcement ---
    claims = _authenticate(token)
    set_user_context(claims)

    audit = AuditLogger(
        cfg.audit_log_path,
        username=claims.get("sub", "anonymous"),
        role=claims.get("role", ""),
    )
    audit.start(question)

    from langchain_core.messages import SystemMessage
    initial_messages = [
        SystemMessage(content=SYSTEM_MESSAGE),
        HumanMessage(content=question),
    ]

    try:
        final_state = build_graph().invoke(
            {"messages": initial_messages, "audit": audit, "claims": claims}
        )
        
        # Get the final answer from the last message
        last_msg = final_state["messages"][-1]
        answer: str = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)
        answer = answer or ""
        
        # Check if request was denied by guardrail
        if final_state.get("_guardrail_denied"):
            # Guardrail denial — return the denial message as-is
            audit.event("request.completed", status="success", final_output=answer)
            return answer
        
        audit.event("request.completed", status="success", final_output=answer)
        return answer
    except Exception as exc:
        audit.event("request.failed", status="failure", error=repr(exc))
        raise


def run_question_with_callback(question: str, on_event, token: str | None = None) -> str:
    """Like run_question but fires on_event(record) for every audit event.
    Used by the Streamlit UI to stream tool progress in real time.
    """
    # --- Auth enforcement ---
    claims = _authenticate(token)
    set_user_context(claims)

    audit = AuditLogger(
        cfg.audit_log_path,
        on_event=on_event,
        username=claims.get("sub", "anonymous"),
        role=claims.get("role", ""),
    )
    audit.start(question)

    from langchain_core.messages import SystemMessage
    initial_messages = [
        SystemMessage(content=SYSTEM_MESSAGE),
        HumanMessage(content=question),
    ]

    try:
        final_state = build_graph().invoke(
            {"messages": initial_messages, "audit": audit, "claims": claims}
        )
        
        # Get the final answer from the last message
        last_msg = final_state["messages"][-1]
        answer: str = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)
        answer = answer or ""
        
        # Check if request was denied by guardrail
        if final_state.get("_guardrail_denied"):
            # Guardrail denial — return the denial message as-is
            audit.event("request.completed", status="success", final_output=answer)
            return answer
        
        if not answer.strip():
            raise ValueError("The model returned an empty response. Please try again.")
        audit.event("request.completed", status="success", final_output=answer)
        return answer
    except Exception as exc:
        audit.event("request.failed", status="failure", error=repr(exc))
        raise


def _authenticate(token: str | None) -> dict:
    """Verify JWT and return claims. Raises AuthError if invalid or missing."""
    if not token:
        raise AuthError("Authentication required. Please log in.")
    return verify_token(token)
