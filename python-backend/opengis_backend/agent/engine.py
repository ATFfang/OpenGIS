"""GIS Agent Engine — public runtime facade for autonomous GIS analysis.

The LLM decides at each step whether to call a tool or reply with text.

Flow:
┌──────────┐     ┌──────────────┐     ┌──────────┐     ┌──────────┐
│  Reason  │────►│ Text reply   │     │          │     │          │
│  (Think) │     │  OR tool     │────►│ Runtime  │────►│ Observe  │
└──────────┘     └──────────────┘     │  exec    │     │ result   │
     ▲                                └──────────┘     └──────────┘
     └────────────────── Loop ────────────────────────────────┘
"""

from opengis_backend.agent.open_gis_agent import OpenGISAgent
from opengis_backend.agent.telemetry.events import AgentEvent, AgentEventType

# Canonical short alias used by the RPC layer.
GISAgent = OpenGISAgent

__all__ = ["AgentEvent", "AgentEventType", "GISAgent", "OpenGISAgent"]
