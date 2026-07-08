"""
GIS Agent Engine — function-call loop for autonomous GIS analysis.

v3.1 (2026-04): Custom agent loop replaces smolagents CodeAgent.
The LLM decides at each step whether to call a tool or reply with text.

Flow:
┌──────────┐     ┌──────────────┐     ┌──────────┐     ┌──────────┐
│  Reason  │────►│ Text reply   │     │          │     │          │
│  (Think) │     │  OR tool     │────►│ Runtime  │────►│ Observe  │
└──────────┘     └──────────────┘     │  exec    │     │ result   │
     ▲                                └──────────┘     └──────────┘
     └────────────────── Loop ────────────────────────────────┘
"""

from opengis_backend.agent.code_agent import GISCodeAgent
from opengis_backend.agent.events import AgentEvent, AgentEventType

# Canonical short alias used by the RPC layer.
GISAgent = GISCodeAgent

__all__ = ["AgentEvent", "AgentEventType", "GISAgent", "GISCodeAgent"]
