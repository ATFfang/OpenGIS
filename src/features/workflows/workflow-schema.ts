/**
 * Workflow schema — the on-disk / in-memory shape of a workflow document.
 *
 * Design principles (see MEMORY.md & 2026-04-23 daily):
 * - Workflow = visual dual of a Python pipeline. Each node is a *reference*
 *   to a real .py file in `workspace/scripts/` plus a params bag.
 * - The workflow file itself is plain JSON (human-editable), stored at
 *   `workspace/workflows/<name>.flow.json`. YAML was considered but we
 *   already have zero YAML infra in the TS side — JSON is cheaper now and
 *   still round-trips fine; we can add a YAML exporter later if wanted.
 * - The front-end treats the JSON as the single source of truth. The
 *   canvas edits the in-memory `Workflow` object, then serialises on
 *   save. No hidden state.
 * - Version stamped (`schemaVersion`) so we can evolve without breaking
 *   existing files — bump on any breaking change and add a migration.
 */

// ─── Schema version ──────────────────────────────────────────────
// Bump this on breaking changes. The loader must handle older versions.
export const WORKFLOW_SCHEMA_VERSION = 1 as const

// ─── Core types ──────────────────────────────────────────────────

/** 2-D position on the canvas, in CSS pixels relative to the canvas origin. */
export interface NodePosition {
  x: number
  y: number
}

/**
 * Declared input/output port on a node.
 *
 * `type` is a free-form string (e.g. 'GeoDataFrame', 'DataFrame', 'Any').
 * We deliberately do NOT enforce strict type matching on the edge — GIS
 * data types are too varied, and strict validation has killed every
 * workflow tool that tried it (early KNIME, Alteryx). UI layer uses the
 * string only for tooltips and soft-mismatch warnings.
 */
export interface NodePort {
  /** Machine-safe identifier, unique per node (e.g. "gdf", "distance"). */
  name: string
  /** Human label shown on the port handle. Defaults to `name`. */
  label?: string
  /** Free-form type hint, e.g. "GeoDataFrame". */
  type?: string
  /** Optional description shown in tooltip. */
  description?: string
}

/**
 * Hook assertion for a workflow step.
 *
 * Hooks are Python expressions that are evaluated after the step's code
 * runs. If any hook fails, the step is retried or the user is prompted.
 */
export interface StepHook {
  /** Python expression to evaluate (e.g. "len(gdf) > 0"). */
  expression: string
  /** Human-readable description of what this hook checks. */
  description?: string
  /** What to do if the hook fails: retry, ask_user, or skip. */
  onFail?: 'retry' | 'ask_user' | 'skip'
}

/** One node on the canvas / one step in the guided workflow. */
export interface WorkflowNode {
  /** Stable ID, unique within the workflow (e.g. "buffer_1"). */
  id: string
  /** Display title on the node card (e.g. "Compute Buffer"). */
  title: string
  /**
   * Detailed description of what this step should accomplish.
   * In guided mode, this is the primary instruction for the LLM.
   */
  description?: string
  /**
   * Human-authored contract describing what this node expects to receive
   * from upstream nodes. This is intentionally descriptive rather than a
   * strict runtime type: the workflow designer tells the agent what prior
   * outputs mean and how they should be consumed.
   */
  inputContract?: string
  /**
   * Human-authored contract describing what this node must hand off to
   * downstream nodes. The backend prompt treats this as a high-priority
   * deliverable and asks the agent to surface exact paths/layer ids/metrics
   * in the step summary.
   */
  outputContract?: string
  /**
   * Path to the backing Python script, *relative to the workspace root*.
   * Empty string for placeholder / not-yet-bound nodes — this lets users
   * drag out empty nodes and bind scripts later.
   */
  scriptPath: string
  /** Inputs declared by the script (or user-overridden). */
  inputs: NodePort[]
  /** Outputs declared by the script. */
  outputs: NodePort[]
  /** Parameter bag (serialisable JSON). Keys match param names. */
  params: Record<string, unknown>
  /** Canvas position. */
  position: NodePosition
  /** Optional free-form note the user can attach. */
  notes?: string
  /**
   * Hook assertions to validate after this step completes.
   * Each hook is a Python expression that must evaluate to True.
   */
  hooks?: StepHook[]
  /** Max retries for this step if hooks fail. Defaults to 3. */
  maxRetries?: number
  /** Node type hint: 'process' | 'input' | 'output' | 'decision'. */
  nodeType?: string
}

/** Edge between two node ports. */
export interface WorkflowEdge {
  /** Stable edge ID. */
  id: string
  /** Source node ID. */
  source: string
  /** Source port `name` on that node. */
  sourceHandle: string
  /** Target node ID. */
  target: string
  /** Target port `name` on that node. */
  targetHandle: string
}

/** The entire on-disk document. */
export interface Workflow {
  schemaVersion: typeof WORKFLOW_SCHEMA_VERSION
  /** Human-readable name, mirrors the filename stem by convention. */
  name: string
  /** Optional longer description. */
  description?: string
  /** ISO timestamp — last saved. */
  updatedAt: string
  /** ISO timestamp — first created. */
  createdAt: string
  /** Nodes on the canvas. */
  nodes: WorkflowNode[]
  /** Edges connecting nodes. */
  edges: WorkflowEdge[]
  /**
   * Viewport state (for restoring pan/zoom on reopen). Optional because
   * the canvas library can recompute a fit-view on load.
   */
  viewport?: {
    x: number
    y: number
    zoom: number
  }
}

// ─── Factory / helpers ───────────────────────────────────────────

/**
 * Return a brand-new empty workflow document.
 * Used when the user clicks "+ New Workflow".
 */
export function createEmptyWorkflow(name: string): Workflow {
  const now = new Date().toISOString()
  return {
    schemaVersion: WORKFLOW_SCHEMA_VERSION,
    name,
    description: '',
    createdAt: now,
    updatedAt: now,
    nodes: [],
    edges: [],
  }
}

/**
 * Parse a raw string (from disk) into a Workflow, with defensive
 * fallbacks for older/partial files. Throws if the JSON is unparseable
 * or has an incompatible schemaVersion we can't upgrade.
 */
export function parseWorkflow(raw: string, fallbackName = 'Untitled'): Workflow {
  let obj: any
  try {
    obj = JSON.parse(raw)
  } catch (err) {
    throw new Error(`Invalid workflow JSON: ${(err as Error).message}`)
  }

  if (typeof obj !== 'object' || obj === null) {
    throw new Error('Workflow file is not a JSON object')
  }

  const version = obj.schemaVersion
  if (typeof version !== 'number') {
    // Pre-version files: treat as v1 with whatever's there.
    obj.schemaVersion = WORKFLOW_SCHEMA_VERSION
  } else if (version > WORKFLOW_SCHEMA_VERSION) {
    throw new Error(
      `Workflow was saved by a newer version of OpenGIS (schema v${version}). ` +
      `Please upgrade.`
    )
  }

  const now = new Date().toISOString()
  return {
    schemaVersion: WORKFLOW_SCHEMA_VERSION,
    name: typeof obj.name === 'string' ? obj.name : fallbackName,
    description: typeof obj.description === 'string' ? obj.description : '',
    createdAt: typeof obj.createdAt === 'string' ? obj.createdAt : now,
    updatedAt: typeof obj.updatedAt === 'string' ? obj.updatedAt : now,
    nodes: Array.isArray(obj.nodes) ? obj.nodes.map(normaliseNode) : [],
    edges: Array.isArray(obj.edges) ? obj.edges.map(normaliseEdge) : [],
    viewport: obj.viewport && typeof obj.viewport === 'object'
      ? {
          x: Number(obj.viewport.x) || 0,
          y: Number(obj.viewport.y) || 0,
          zoom: Number(obj.viewport.zoom) || 1,
        }
      : undefined,
  }
}

function normaliseNode(n: any): WorkflowNode {
  return {
    id: String(n?.id ?? `node_${Math.random().toString(36).slice(2, 8)}`),
    title: String(n?.title ?? 'Untitled Node'),
    description: typeof n?.description === 'string' ? n.description : undefined,
    inputContract: typeof n?.inputContract === 'string'
      ? n.inputContract
      : typeof n?.input_contract === 'string'
        ? n.input_contract
        : undefined,
    outputContract: typeof n?.outputContract === 'string'
      ? n.outputContract
      : typeof n?.output_contract === 'string'
        ? n.output_contract
        : undefined,
    scriptPath: typeof n?.scriptPath === 'string' ? n.scriptPath : '',
    inputs: Array.isArray(n?.inputs) ? n.inputs.map(normalisePort) : [],
    outputs: Array.isArray(n?.outputs) ? n.outputs.map(normalisePort) : [],
    params: (n?.params && typeof n.params === 'object') ? n.params : {},
    position: {
      x: Number(n?.position?.x) || 0,
      y: Number(n?.position?.y) || 0,
    },
    notes: typeof n?.notes === 'string' ? n.notes : undefined,
    hooks: Array.isArray(n?.hooks) ? n.hooks.map(normaliseHook) : undefined,
    maxRetries: typeof n?.maxRetries === 'number' ? n.maxRetries : undefined,
    nodeType: typeof n?.nodeType === 'string' ? n.nodeType : undefined,
  }
}

function normaliseHook(h: any): StepHook {
  return {
    expression: String(h?.expression ?? ''),
    description: typeof h?.description === 'string' ? h.description : undefined,
    onFail: ['retry', 'ask_user', 'skip'].includes(h?.onFail) ? h.onFail : undefined,
  }
}

function normalisePort(p: any): NodePort {
  return {
    name: String(p?.name ?? ''),
    label: typeof p?.label === 'string' ? p.label : undefined,
    type: typeof p?.type === 'string' ? p.type : undefined,
    description: typeof p?.description === 'string' ? p.description : undefined,
  }
}

function normaliseEdge(e: any): WorkflowEdge {
  return {
    id: String(e?.id ?? `edge_${Math.random().toString(36).slice(2, 8)}`),
    source: String(e?.source ?? ''),
    sourceHandle: String(e?.sourceHandle ?? ''),
    target: String(e?.target ?? ''),
    targetHandle: String(e?.targetHandle ?? ''),
  }
}

/**
 * Serialise a workflow for on-disk storage. Pretty-printed so it
 * round-trips cleanly through git and is human-editable.
 */
export function serialiseWorkflow(wf: Workflow): string {
  const clean: Workflow = {
    ...wf,
    schemaVersion: WORKFLOW_SCHEMA_VERSION,
    updatedAt: new Date().toISOString(),
  }
  return JSON.stringify(clean, null, 2)
}

// ─── Filesystem conventions ──────────────────────────────────────

/** The subdirectory (relative to workspace root) where workflows live. */
export const WORKFLOW_DIR_NAME = 'workflows'
/** The on-disk extension for workflow files. Picked so they're distinct
 *  from generic JSON and easy to glob. */
export const WORKFLOW_FILE_EXT = '.flow.json'

/** Check whether a filename looks like a workflow document. */
export function isWorkflowFilename(name: string): boolean {
  return name.toLowerCase().endsWith(WORKFLOW_FILE_EXT)
}
