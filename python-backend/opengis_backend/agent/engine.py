"""
GIS Agent Engine — Hybrid CodeAct loop for autonomous GIS analysis.

v3.1 (2026-04): Custom agent loop replaces smolagents CodeAgent.
The LLM decides at each step whether to reply with text or execute code.

Flow:
┌──────────┐     ┌──────────────┐     ┌──────────┐     ┌──────────┐
│  Reason  │────►│ Text reply   │     │          │     │          │
│  (Think) │     │  OR code     │────►│ Sandbox  │────►│ Observe  │
└──────────┘     └──────────────┘     │  exec    │     │ result   │
     ▲                                └──────────┘     └──────────┘
     └────────────────── Loop ────────────────────────────────┘
"""

from opengis_backend.agent.code_agent import GISCodeAgent
from opengis_backend.agent.events import AgentEvent, AgentEventType

# Backward-compatible name for protocol.py and other importers.
GISAgent = GISCodeAgent

__all__ = ["AgentEvent", "AgentEventType", "GISAgent", "GISCodeAgent"]
