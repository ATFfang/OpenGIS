# OpenGIS Agent Architecture Upgrade

This document defines the target architecture for upgrading OpenGIS from a
single custom loop into a mainstream, multi-agent runtime while preserving the
Python GIS execution core.

## Target Shape

OpenGIS should separate the agent system into five layers:

1. **Agent profiles**
   - Named agent roles such as `gis-build`, `gis-plan`, `gis-explore`,
     `gis-report`, and `workflow-runner`.
   - Each profile owns its prompt, model preferences, step budget, tool groups,
     and permission policy.

2. **Session runtime**
   - Every user turn runs inside a session.
   - Subagents and workflow nodes become child sessions, not anonymous helper
     calls.
   - A parent session stores only compact child summaries in context, while the
     full child run remains inspectable in the run archive.

3. **Tool runtime**
   - All tool/function calls pass through one runtime.
   - The runtime handles schema, permissions, execution, result normalization,
     truncation, telemetry, and archive metadata.
   - Code execution is just one tool (`execute_code`), not a special loop path.

4. **Permission runtime**
   - Tool calls are evaluated before execution.
   - Decisions are `allow`, `ask`, or `deny`.
   - Policies can be configured globally, per agent profile, and per tool.
   - High-risk actions include shell execution, package installation, network
     access, file deletion, external-path writes, and destructive GIS/database
     operations.

5. **Context runtime**
   - Context is composed from pinned facts, user preferences, project memory,
     session summaries, recent turns, and artifact indexes.
   - Compression is handled by hidden/system profiles instead of being embedded
     directly in each loop.

## Migration Strategy

### Phase 1: Stabilize the Hybrid Function-Call Runtime

- Keep Python as the GIS execution plane.
- Route AgentLoop and WorkflowLoop through one `ToolRuntime`.
- Normalize tool result shape.
- Emit tool start/result events consistently.
- Remove `final_answer` as a control primitive and make text completion the
  canonical stop signal.

### Phase 2: Add Mainstream Agent Control Plane

- Introduce `AgentProfile`, `PermissionRuntime`, and `AgentSession`.
- Map chat to `gis-build` by default.
- Map planning-only requests to `gis-plan`.
- Map subagents to child sessions with isolated context and full archived runs.
- Map workflows to a planner/orchestrator that invokes the same runtime rather
  than maintaining a separate tool protocol.

### Phase 3: Harden Tool Governance

- Add pre-execution permission checks.
- Add UI approval for `ask`.
- Add persistent user approvals scoped by workspace/profile/tool/pattern.
- Add structured tool results:
  - `title`
  - `output`
  - `metadata`
  - `artifacts`
  - `truncated`
  - `error`

### Phase 4: Collapse Duplicate Loops

- Keep `AgentLoop` as the primitive step runner.
- Convert `WorkflowLoop` into a DAG orchestrator over child sessions.
- Convert `run_subagent` into a child-session launcher.
- Store all sessions under one run tree.

## Non-Goals

- Do not migrate GIS computation to TypeScript.
- Do not replace GeoPandas/Rasterio/QGIS integrations with browser-only logic.
- Do not block existing runs by enabling strict permissions before the approval
  UI exists.

## First Compatible Runtime Contract

```text
AgentProfile
  -> PermissionRuntime
  -> ToolRuntime
  -> Executor / SkillRegistry
  -> AgentSession archive and UI events
```

The first implementation keeps default permissions non-enforcing for
compatibility. The key architecture change is that all future safety, UI
approval, subagent, workflow, and archive behavior has one place to attach.

## Queue / Inbox Runtime

The compatible queue layer separates prompt admission from execution without
breaking the current streaming UI:

```text
chat.user_message
  -> AgentInboxItem(status=accepted)
  -> AgentQueueItem(status=queued)
  -> _execute_agent_queue_item(...)
  -> AgentSession + RunArchive + tool lifecycle events
```

New control-plane RPCs:

- `rpc.agent.inbox.list`: lists durable workspace prompt admission records from
  `.opengis/sessions.json`.
- `rpc.agent.queue.submit`: creates an inbox item and an in-process queue item
  but does not execute it.
- `rpc.agent.queue.run`: executes a queued item by `queue_id` using the same
  streaming event path as `chat.user_message`.
- `rpc.agent.queue.get`: returns one queue item by `queue_id` or `inbox_id`.
- `rpc.agent.queue.resume`: restores resumable workspace inbox records into
  the in-process queue.
- `rpc.agent.queue.retry`: moves an `error` or `cancelled` item back to
  `queued`.
- `rpc.agent.queue.cancel`: cancels a queued item, or delegates to the active
  agent interrupt path for a running item.
- `rpc.agent.queue.process`: consumes queued items for a workspace through a
  workspace-scoped processor path.
- `rpc.agent.queue.list`: lists in-process queue items for the current backend.

This is intentionally a bridge design. The existing `chat.user_message` call
still waits for completion, while newer clients can move to
`queue.submit -> queue.run` or `queue.submit -> queue.process`. Queue items are
process-local; the durable recovery source is the workspace inbox. Plain chat
items are restored from `.opengis/sessions.json`; workflow attachments are
persisted under `.opengis/workflows/<workflow_id>.flow.json` and can be
reloaded when queue items are resumed.

## Permission Enforcement Switch

Default profiles run in audit mode: risky tool calls are recorded, but not
blocked. To enable interactive approvals for one workspace, install profiles
with `rpc.agent.profiles.install_defaults`, then set a profile metadata flag:

```json
{
  "profiles": [
    {
      "name": "gis-build",
      "mode": "build",
      "description": "Default autonomous GIS task execution agent.",
      "permission_level": "safe_write",
      "metadata": {
        "permission_enforce": true,
        "permission_default": "allow",
        "permission_tool_overrides": {
          "delete_file": "ask"
        },
        "permission_rules": [
          {
            "tool": "write_*",
            "action": "ask",
            "reason": "Writing files requires confirmation."
          }
        ]
      }
    }
  ]
}
```

When enforcement is enabled, `PermissionRuntime` sends `ask` decisions through
the frontend `rpc.ui.ask.*` request handlers. User denial returns a structured
tool error instead of executing the tool. Permission requests are observable
through `rpc.agent.permissions.list`, which returns pending and resolved
requests for the current backend process.

Persisted permission rules live in `.opengis/permissions.json` and are applied
before profile/risk rules. They are managed through:

- `rpc.agent.permissions.rules.list`
- `rpc.agent.permissions.rules.add`
- `rpc.agent.permissions.rules.remove`

The Runs panel includes a compact control-plane summary for queue items and
persisted permission rules so these backend controls are visible in the UI.
