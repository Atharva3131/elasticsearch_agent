"""Streamlit chat interface for the Elasticsearch AI agent."""
from __future__ import annotations

import queue
import re
import threading
from pathlib import Path

import streamlit as st

from elasticsearch_agent.agent.workflow import run_question_with_callback

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Elasticsearch AI Agent",
    page_icon="🔍",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Custom CSS — compact header, full-height chat
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    /* Shrink the main title */
    h1 { font-size: 1.2rem !important; margin-bottom: 0.2rem !important; }

    /* Push chat input to bottom and let messages fill the space */
    .stChatFloatingInputContainer { bottom: 1rem; }

    /* Tighten top padding */
    .block-container { padding-top: 1rem !important; }

    /* Typing dots animation */
    @keyframes blink {
        0%   { opacity: 0.2; }
        20%  { opacity: 1;   }
        100% { opacity: 0.2; }
    }
    .typing-dot {
        display: inline-block;
        width: 6px; height: 6px;
        border-radius: 50%;
        background: #888;
        margin: 0 2px;
        animation: blink 1.4s infinite both;
    }
    .typing-dot:nth-child(2) { animation-delay: 0.2s; }
    .typing-dot:nth-child(3) { animation-delay: 0.4s; }
    .typing-indicator {
        display: inline-flex;
        align-items: center;
        gap: 2px;
        padding: 4px 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown("")
st.caption("")

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "messages" not in st.session_state:
    # Each entry: {"role": "user"|"assistant", "content": str, "chart": str|None, "steps": list}
    st.session_state.messages = []

# ---------------------------------------------------------------------------
# Tool icons
# ---------------------------------------------------------------------------

_TOOL_ICONS = {
    "elastic_index_search_tool": "🔎",
    "elastic_index_count_tool": "🔢",
    "elastic_index_show_data": "📄",
    "elastic_index_show_details": "🗂️",
    "elastic_list_indices": "📋",
    "create_pie_chart": "🥧",
}

# ---------------------------------------------------------------------------
# Render existing chat history
# ---------------------------------------------------------------------------

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        # Replay tool steps for assistant messages
        if msg["role"] == "assistant" and msg.get("steps"):
            with st.expander("🛠 Tool calls", expanded=False):
                for s in msg["steps"]:
                    st.markdown(s)
        st.markdown(msg["content"])
        if msg.get("chart") and Path(msg["chart"]).exists():
            st.image(msg["chart"], caption=Path(msg["chart"]).stem.replace("_", " ").title())

# ---------------------------------------------------------------------------
# Chat input
# ---------------------------------------------------------------------------

TYPING_HTML = (
    '<div class="typing-indicator">'
    '<span class="typing-dot"></span>'
    '<span class="typing-dot"></span>'
    '<span class="typing-dot"></span>'
    "</div>"
)

if prompt := st.chat_input("Ask a question about your data..."):

    # Show user message.
    st.session_state.messages.append({"role": "user", "content": prompt, "chart": None, "steps": []})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):

        # Typing dots while waiting.
        typing_placeholder = st.empty()
        typing_placeholder.markdown(TYPING_HTML, unsafe_allow_html=True)

        status_placeholder = st.empty()
        answer_placeholder = st.empty()
        chart_placeholder = st.empty()

        # Thread-safe event queue.
        event_queue: queue.Queue = queue.Queue()
        result_holder: list = []
        done_event = threading.Event()

        def on_event(record: dict):
            event_queue.put(record)

        def worker():
            try:
                answer = run_question_with_callback(prompt, on_event)
                result_holder.append(answer)
            except Exception as exc:
                result_holder.append(None)
                result_holder.append(exc)
            finally:
                done_event.set()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        # Drain events and update UI.
        tool_steps: list[str] = []
        chart_path: str | None = None
        active_tool: str = ""

        while not done_event.is_set() or not event_queue.empty():
            try:
                record = event_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            event = record.get("event", "")
            tool = record.get("tool", "")
            reason = record.get("reason", "")
            icon = _TOOL_ICONS.get(tool, "🔧")

            if event == "tool.started":
                active_tool = tool
                step = f"{icon} **`{tool}`**"
                if reason:
                    step += f"\n\n  > _{reason}_"
                tool_steps.append(step)

                with status_placeholder.status("⚙️ Thinking...", expanded=True):
                    for s in tool_steps:
                        st.markdown(s)
                        st.divider()

            elif event == "tool.completed":
                duration = record.get("duration_ms", 0)
                duration_str = f"{duration / 1000:.1f}s"
                if tool_steps:
                    tool_steps[-1] += f"\n\n  ✅ completed in _{duration_str}_"
                # Capture chart path as soon as the chart tool completes.
                if record.get("artifact_path"):
                    chart_path = record["artifact_path"].replace("\\", "/")
                with status_placeholder.status("⚙️ Thinking...", expanded=True):
                    for s in tool_steps:
                        st.markdown(s)
                        st.divider()

            elif event == "tool.failed":
                if tool_steps:
                    tool_steps[-1] += "\n\n  ❌ **failed**"
                with status_placeholder.status("⚙️ Thinking...", expanded=True):
                    for s in tool_steps:
                        st.markdown(s)
                        st.divider()

            elif event == "request.completed":
                # Also try scanning final_output for chart path as fallback.
                if not chart_path:
                    final_out = record.get("final_output", "")
                    png_match = re.search(r"artifacts[/\\]charts[/\\][^\s\"']+\.png", str(final_out))
                    if png_match:
                        chart_path = png_match.group(0).replace("\\", "/")

        # Join thread with generous timeout — if the LLM hangs after the chart
        # is already generated, we still show the chart.
        thread.join(timeout=60)

        # Hide typing dots.
        typing_placeholder.empty()

        # Collapse status.
        if tool_steps:
            with status_placeholder.status("✅ Done", expanded=False, state="complete"):
                for s in tool_steps:
                    st.markdown(s)
                    st.divider()
        else:
            status_placeholder.empty()

        # Render answer — handle three cases:
        # 1. Normal: LLM returned an answer
        # 2. Chart generated but LLM hung on final response
        # 3. Error
        answer = result_holder[0] if result_holder and result_holder[0] is not None else None

        if answer:
            # Also scan answer text for chart path if not found via event.
            if not chart_path:
                png_match = re.search(r"artifacts[/\\]charts[/\\][^\s\"']+\.png", answer)
                if png_match:
                    chart_path = png_match.group(0).replace("\\", "/")
            answer_placeholder.markdown(answer)

        elif chart_path:
            # Chart was produced but LLM didn't return final text — show a fallback.
            answer = "✅ Chart generated successfully."
            answer_placeholder.markdown(answer)

        elif len(result_holder) > 1 and result_holder[1] is not None:
            err = f"❌ Request failed: {result_holder[1]}"
            answer_placeholder.error(err)
            st.session_state.messages.append({"role": "assistant", "content": err, "chart": None, "steps": tool_steps})
            answer = None

        else:
            answer_placeholder.error("❌ No response received.")
            answer = None

        # Display chart.
        if chart_path and Path(chart_path).exists():
            chart_placeholder.image(
                chart_path,
                caption=Path(chart_path).stem.replace("_", " ").title(),
                use_container_width=True,
            )

        if answer is not None:
            st.session_state.messages.append({
                "role": "assistant",
                "content": answer,
                "chart": chart_path,
                "steps": tool_steps,
            })
