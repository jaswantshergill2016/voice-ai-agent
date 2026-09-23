"""LangGraph fast-path graph.

A single `respond` node streams LLM tokens. Conversation history is kept per
call in a LangGraph checkpointer keyed by `thread_id` (= Vapi call id), so each
turn only needs to submit the newest user utterance.

Side effects (CRM, logging, SMS) are NOT graph nodes: they are dispatched to
n8n from `main.py` after the stream completes so they never block tokens.
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from config import settings
from llm import get_llm


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


async def respond(state: AgentState, config: RunnableConfig) -> AgentState:
    messages = state["messages"]
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=settings.system_prompt), *messages]
    # Pass config explicitly so token callbacks reach stream_mode="messages"
    # (contextvars do not propagate into async nodes on Python < 3.11).
    reply = None
    async for chunk in get_llm().astream(messages, config):
        reply = chunk if reply is None else reply + chunk
    return {"messages": [reply]}


def build_graph(checkpointer=None):
    g = StateGraph(AgentState)
    g.add_node("respond", respond)
    g.add_edge(START, "respond")
    g.add_edge("respond", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())


graph = build_graph()


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def has_history(thread_id: str) -> bool:
    snapshot = await graph.aget_state(thread_config(thread_id))
    return bool(snapshot.values.get("messages"))
