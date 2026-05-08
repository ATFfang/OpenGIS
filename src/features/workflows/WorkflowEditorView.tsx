/**
 * WorkflowEditorView — Guided step-based workflow editor.
 *
 * Instead of a free-form canvas with nodes and wires, this editor
 * presents a linear step list that the user fills in sequentially.
 * Each step has:
 *   - A title (what this step does)
 *   - A description (detailed instructions for the LLM)
 *   - Optional hooks (Python assertions to validate the step)
 *   - Optional params (key/value config passed to the step)
 *
 * The workflow is executed by the WorkflowLoop in the backend:
 * the LLM receives each step's description as a constrained prompt
 * and must produce code to accomplish it.
 *
 * Design: Guided / wizard-style UI that makes it easy for non-
 * programmers to define repeatable GIS pipelines.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Plus,
  Save,
  Trash2,
  Play,
  GitBranch,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  ArrowDown,
  Zap,
  Shield,
  Settings2,
  X,
  Copy,
  GripVertical,
  CheckCircle2,
  Circle,
} from 'lucide-react'
import type { ViewTab } from '@/stores/viewStore'
import { useWorkflowStore } from '@/stores/workflowStore'
import { useChatStore } from '@/stores/chatStore'
import { useT } from '@/i18n'
import type {
  StepHook,
  WorkflowEdge,
  WorkflowNode,
} from '@/features/workflows/workflow-schema'

// ─── Top-level component ─────────────────────────────────────────

interface WorkflowEditorViewProps {
  tab: ViewTab
}

export function WorkflowEditorView({ tab }: WorkflowEditorViewProps) {
  const path = tab.filePath!
  const loadWorkflow = useWorkflowStore((s) => s.loadWorkflow)
  const saveWorkflow = useWorkflowStore((s) => s.saveWorkflow)
  const loaded = useWorkflowStore((s) => s.loaded[path])

  const [loadError, setLoadError] = useState<string | null>(null)

  // ── Load workflow on mount / path change ──────────────────────
  useEffect(() => {
    let cancelled = false
    setLoadError(null)
    loadWorkflow(path).then((doc) => {
      if (cancelled) return
      if (!doc) {
        setLoadError(useWorkflowStore.getState().error || 'Failed to load workflow')
      }
    })
    return () => {
      cancelled = true
    }
  }, [path, loadWorkflow])

  // ── Ctrl+S / Cmd+S saves ──────────────────────────────────────
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {
        e.preventDefault()
        saveWorkflow(path)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [path, saveWorkflow])

  if (loadError) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-bg-primary">
        <div className="text-center max-w-sm">
          <AlertCircle className="w-10 h-10 text-accent-danger/60 mx-auto mb-3" />
          <p className="text-sm text-text-secondary mb-1">
            Couldn't open this workflow
          </p>
          <p className="text-xs text-text-muted">{loadError}</p>
        </div>
      </div>
    )
  }

  if (!loaded) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-bg-primary">
        <p className="text-xs text-text-muted">Loading workflow…</p>
      </div>
    )
  }

  return <GuidedWorkflowEditor path={path} />
}

// ─── Guided Workflow Editor ──────────────────────────────────────

function GuidedWorkflowEditor({ path }: { path: string }) {
  const t = useT()
  const loaded = useWorkflowStore((s) => s.loaded[path])
  const addNode = useWorkflowStore((s) => s.addNode)
  const updateNode = useWorkflowStore((s) => s.updateNode)
  const removeNode = useWorkflowStore((s) => s.removeNode)
  const addEdge = useWorkflowStore((s) => s.addEdge)
  const removeEdge = useWorkflowStore((s) => s.removeEdge)
  const saveWorkflow = useWorkflowStore((s) => s.saveWorkflow)
  const updateLoaded = useWorkflowStore((s) => s.updateLoaded)
  const sendMessage = useChatStore((s) => s.sendMessage)

  const doc = loaded!.doc
  const isDirty = loaded!.dirty

  const [selectedStepId, setSelectedStepId] = useState<string | null>(null)
  const [isRunning, setIsRunning] = useState(false)

  // ── Drag-and-drop state ───────────────────────────────────────
  const [draggedStepId, setDraggedStepId] = useState<string | null>(null)
  const [dragOverStepId, setDragOverStepId] = useState<string | null>(null)
  const [dragOverPosition, setDragOverPosition] = useState<'above' | 'below' | null>(null)

  // Steps are nodes in order (edges define the sequence)
  const steps = useMemo(() => {
    // Build adjacency from edges
    const nextMap = new Map<string, string>()
    const prevMap = new Map<string, string>()
    for (const edge of doc.edges) {
      nextMap.set(edge.source, edge.target)
      prevMap.set(edge.target, edge.source)
    }

    // Find the first node (no incoming edge)
    const startNodes = doc.nodes.filter((n) => !prevMap.has(n.id))

    if (startNodes.length === 0 && doc.nodes.length > 0) {
      // Fallback: just return nodes in array order
      return doc.nodes
    }

    // Walk the chain from the first start node
    const ordered: WorkflowNode[] = []
    const visited = new Set<string>()
    let current = startNodes[0]?.id

    while (current && !visited.has(current)) {
      visited.add(current)
      const node = doc.nodes.find((n) => n.id === current)
      if (node) ordered.push(node)
      current = nextMap.get(current) || ''
    }

    // Add any unlinked nodes at the end
    for (const node of doc.nodes) {
      if (!visited.has(node.id)) {
        ordered.push(node)
      }
    }

    return ordered
  }, [doc.nodes, doc.edges])

  const selectedStep = selectedStepId
    ? steps.find((s) => s.id === selectedStepId) || null
    : null

  // ── Add a new step ────────────────────────────────────────────
  const handleAddStep = useCallback(() => {
    const stepNum = steps.length + 1
    const newNode: WorkflowNode = {
      id: `step_${Date.now().toString(36)}`,
      title: `Step ${stepNum}`,
      description: '',
      scriptPath: '',
      inputs: [],
      outputs: [],
      params: {},
      position: { x: 0, y: stepNum * 100 },
      hooks: [],
      maxRetries: 3,
    }

    addNode(path, newNode)

    // Auto-link: connect the last step to this new one
    if (steps.length > 0) {
      const lastStep = steps[steps.length - 1]
      addEdge(path, {
        id: `edge_${Date.now().toString(36)}`,
        source: lastStep.id,
        sourceHandle: 'output',
        target: newNode.id,
        targetHandle: 'input',
      })
    }

    setSelectedStepId(newNode.id)
  }, [path, steps, addNode, addEdge])

  // ── Remove a step ─────────────────────────────────────────────
  const handleRemoveStep = useCallback((stepId: string) => {
    const stepIndex = steps.findIndex((s) => s.id === stepId)

    // Re-link: connect prev → next to maintain the chain
    if (stepIndex > 0 && stepIndex < steps.length - 1) {
      const prev = steps[stepIndex - 1]
      const next = steps[stepIndex + 1]
      addEdge(path, {
        id: `edge_relink_${Date.now().toString(36)}`,
        source: prev.id,
        sourceHandle: 'output',
        target: next.id,
        targetHandle: 'input',
      })
    }

    removeNode(path, stepId)
    if (selectedStepId === stepId) {
      setSelectedStepId(null)
    }
  }, [path, steps, selectedStepId, removeNode, addEdge])

  // ── Reorder steps (used by both buttons and drag-drop) ────────
  const reorderSteps = useCallback((newOrder: WorkflowNode[]) => {
    // Remove all existing sequential edges
    const edgeIds = doc.edges.map((e) => e.id)
    for (const eid of edgeIds) {
      removeEdge(path, eid)
    }

    // Re-add edges in new order
    for (let i = 0; i < newOrder.length - 1; i++) {
      addEdge(path, {
        id: `edge_${Date.now().toString(36)}_${i}`,
        source: newOrder[i].id,
        sourceHandle: 'output',
        target: newOrder[i + 1].id,
        targetHandle: 'input',
      })
    }
  }, [path, doc.edges, removeEdge, addEdge])

  // ── Drag-and-drop handlers ────────────────────────────────────
  const handleDragStart = useCallback((e: React.DragEvent, stepId: string) => {
    setDraggedStepId(stepId)
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', stepId)
    // Make the drag image slightly transparent
    if (e.currentTarget instanceof HTMLElement) {
      e.currentTarget.style.opacity = '0.5'
    }
  }, [])

  const handleDragEnd = useCallback((e: React.DragEvent) => {
    if (e.currentTarget instanceof HTMLElement) {
      e.currentTarget.style.opacity = '1'
    }
    setDraggedStepId(null)
    setDragOverStepId(null)
    setDragOverPosition(null)
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent, stepId: string) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (stepId === draggedStepId) return

    // Determine if dropping above or below
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const midY = rect.top + rect.height / 2
    const position = e.clientY < midY ? 'above' : 'below'

    setDragOverStepId(stepId)
    setDragOverPosition(position)
  }, [draggedStepId])

  const handleDragLeave = useCallback(() => {
    setDragOverStepId(null)
    setDragOverPosition(null)
  }, [])

  const handleDrop = useCallback((e: React.DragEvent, targetStepId: string) => {
    e.preventDefault()
    if (!draggedStepId || draggedStepId === targetStepId) return

    const fromIdx = steps.findIndex((s) => s.id === draggedStepId)
    const toIdx = steps.findIndex((s) => s.id === targetStepId)
    if (fromIdx < 0 || toIdx < 0) return

    const newOrder = [...steps]
    const [moved] = newOrder.splice(fromIdx, 1)

    // Calculate insert position based on drag position
    let insertIdx = toIdx
    if (fromIdx < toIdx) insertIdx-- // Adjust for removal
    if (dragOverPosition === 'below') insertIdx++

    newOrder.splice(insertIdx, 0, moved)
    reorderSteps(newOrder)

    setDraggedStepId(null)
    setDragOverStepId(null)
    setDragOverPosition(null)
  }, [draggedStepId, dragOverPosition, steps, reorderSteps])

  // ── Duplicate step ────────────────────────────────────────────
  const handleDuplicateStep = useCallback((stepId: string) => {
    const step = steps.find((s) => s.id === stepId)
    if (!step) return

    const newNode: WorkflowNode = {
      ...step,
      id: `step_${Date.now().toString(36)}`,
      title: `${step.title} (copy)`,
      position: { x: step.position.x, y: step.position.y + 100 },
    }

    addNode(path, newNode)

    // Link after the original step
    const stepIdx = steps.findIndex((s) => s.id === stepId)
    if (stepIdx < steps.length - 1) {
      // Remove edge from original → next
      const nextStep = steps[stepIdx + 1]
      const edgeToRemove = doc.edges.find(
        (e) => e.source === stepId && e.target === nextStep.id
      )
      if (edgeToRemove) removeEdge(path, edgeToRemove.id)

      // Add: original → copy → next
      addEdge(path, {
        id: `edge_dup1_${Date.now().toString(36)}`,
        source: stepId,
        sourceHandle: 'output',
        target: newNode.id,
        targetHandle: 'input',
      })
      addEdge(path, {
        id: `edge_dup2_${Date.now().toString(36)}`,
        source: newNode.id,
        sourceHandle: 'output',
        target: nextStep.id,
        targetHandle: 'input',
      })
    } else {
      // Append at end
      addEdge(path, {
        id: `edge_dup_${Date.now().toString(36)}`,
        source: stepId,
        sourceHandle: 'output',
        target: newNode.id,
        targetHandle: 'input',
      })
    }

    setSelectedStepId(newNode.id)
  }, [path, steps, doc.edges, addNode, addEdge, removeEdge])

  // ── Run workflow ──────────────────────────────────────────────
  const handleRun = useCallback(async () => {
    if (isRunning) return

    // Save first if dirty
    if (isDirty) {
      await saveWorkflow(path)
    }

    setIsRunning(true)
    try {
      // Send the workflow file as an attachment to the chat
      const workflowName = doc.name || 'Workflow'
      await sendMessage(
        `Run the workflow "${workflowName}"`,
        undefined,
        [{ name: `${workflowName}.flow.json`, path, type: 'workflow' as const }]
      )
    } catch (err) {
      console.error('Failed to run workflow:', err)
    } finally {
      setIsRunning(false)
    }
  }, [isRunning, isDirty, path, doc.name, saveWorkflow, sendMessage])

  return (
    <div className="w-full h-full flex bg-bg-primary">
      {/* Left: Step list */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Toolbar */}
        <div className="h-10 border-b border-border flex items-center px-3 gap-2 shrink-0 bg-bg-secondary">
          <GitBranch className="w-4 h-4 text-accent-geo shrink-0" />
          <span className="text-xs font-medium text-text-primary truncate">
            {doc.name}
            {isDirty && <span className="text-accent-warning ml-1">●</span>}
          </span>

          <div className="flex-1" />

          <span className="text-2xs text-text-muted">
            {steps.length} {steps.length !== 1 ? t.workflow.steps : t.workflow.step}
          </span>

          <ToolbarButton
            icon={<Plus className="w-3.5 h-3.5" />}
            label={t.workflow.addStep}
            onClick={handleAddStep}
          />
          <ToolbarButton
            icon={<Save className="w-3.5 h-3.5" />}
            label={isDirty ? t.workflow.saveShortcut : t.workflow.saved}
            onClick={() => saveWorkflow(path)}
            disabled={!isDirty}
            accent={isDirty}
          />
          <ToolbarButton
            icon={<Play className="w-3.5 h-3.5" />}
            label={isRunning ? t.common.running : t.common.run}
            onClick={handleRun}
            disabled={steps.length === 0 || isRunning}
            accent={!isRunning && steps.length > 0}
          />
        </div>

        {/* Step list */}
        <div className="flex-1 overflow-y-auto scrollbar-thin">
          {steps.length === 0 ? (
            <EmptyWorkflowState onAddStep={handleAddStep} />
          ) : (
            <div className="p-4 space-y-1">
              {/* Workflow description */}
              <div className="mb-4 px-3">
                <input
                  value={doc.name}
                  onChange={(e) => updateLoaded(path, { name: e.target.value })}
                  className="w-full text-base font-semibold text-text-primary bg-transparent outline-none border-b border-transparent hover:border-border focus:border-accent-primary transition-colors pb-1"
                  placeholder="Workflow name..."
                />
                <textarea
                  value={doc.description ?? ''}
                  onChange={(e) => updateLoaded(path, { description: e.target.value })}
                  placeholder="Describe what this workflow does..."
                  rows={2}
                  className="w-full mt-2 text-xs text-text-secondary bg-transparent outline-none border-b border-transparent hover:border-border focus:border-accent-primary transition-colors resize-none leading-relaxed"
                />
              </div>

              {/* Steps */}
              {steps.map((step, index) => (
                <div
                  key={step.id}
                  draggable
                  onDragStart={(e) => handleDragStart(e, step.id)}
                  onDragEnd={handleDragEnd}
                  onDragOver={(e) => handleDragOver(e, step.id)}
                  onDragLeave={handleDragLeave}
                  onDrop={(e) => handleDrop(e, step.id)}
                  className={`
                    transition-transform duration-150
                    ${draggedStepId === step.id ? 'opacity-50' : ''}
                    ${dragOverStepId === step.id && dragOverPosition === 'above' ? 'border-t-2 border-t-accent-primary pt-1' : ''}
                    ${dragOverStepId === step.id && dragOverPosition === 'below' ? 'border-b-2 border-b-accent-primary pb-1' : ''}
                  `}
                >
                  <StepCard
                    step={step}
                    index={index}
                    totalSteps={steps.length}
                    isSelected={step.id === selectedStepId}
                    isDragging={draggedStepId === step.id}
                    onClick={() => setSelectedStepId(
                      selectedStepId === step.id ? null : step.id
                    )}
                    onRemove={() => handleRemoveStep(step.id)}
                    onDuplicate={() => handleDuplicateStep(step.id)}
                  />
                  {/* Connector arrow between steps */}
                  {index < steps.length - 1 && !draggedStepId && (
                    <div className="flex justify-center py-0.5">
                      <ArrowDown className="w-3.5 h-3.5 text-border" />
                    </div>
                  )}
                </div>
              ))}

              {/* Add step button at bottom */}
              <div className="pt-2 flex justify-center">
                <button
                  onClick={handleAddStep}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-text-muted hover:text-accent-primary border border-dashed border-border hover:border-accent-primary/50 rounded-lg transition-colors"
                >
                  <Plus className="w-3 h-3" />
                  {t.workflow.addStep}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Right: Step detail inspector */}
      <div className="w-[300px] shrink-0 border-l border-border bg-bg-secondary flex flex-col">
        {selectedStep ? (
          <StepInspector
            step={selectedStep}
            stepIndex={steps.findIndex((s) => s.id === selectedStep.id)}
            totalSteps={steps.length}
            onUpdate={(patch) => updateNode(path, selectedStep.id, patch)}
            onRemove={() => handleRemoveStep(selectedStep.id)}
          />
        ) : (
          <WorkflowOverview
            path={path}
            doc={doc}
            steps={steps}
            onUpdateDoc={(patch) => updateLoaded(path, patch)}
          />
        )}
      </div>
    </div>
  )
}

// ─── Step Card ───────────────────────────────────────────────────

interface StepCardProps {
  step: WorkflowNode
  index: number
  totalSteps: number
  isSelected: boolean
  isDragging: boolean
  onClick: () => void
  onRemove: () => void
  onDuplicate: () => void
}

function StepCard({
  step,
  index,
  isSelected,
  isDragging,
  onClick,
  onRemove,
  onDuplicate,
}: StepCardProps) {
  const hasDescription = !!step.description?.trim()
  const hasHooks = (step.hooks?.length ?? 0) > 0
  const hasParams = Object.keys(step.params || {}).length > 0

  return (
    <div
      onClick={onClick}
      className={`
        group relative rounded-xl border transition-all duration-150 cursor-pointer select-none
        ${isDragging ? 'opacity-50 scale-[0.98]' : ''}
        ${isSelected
          ? 'border-accent-primary bg-accent-primary/5 shadow-sm shadow-accent-primary/10'
          : 'border-border bg-bg-secondary hover:border-accent-primary/30 hover:shadow-sm'}
      `}
    >
      <div className="flex items-start gap-2 px-2 py-3">
        {/* Drag handle */}
        <div
          className="w-5 h-7 flex items-center justify-center shrink-0 cursor-grab active:cursor-grabbing text-text-muted/40 hover:text-text-muted mt-0.5"
          title="Drag to reorder"
          onMouseDown={(e) => e.stopPropagation()}
        >
          <GripVertical className="w-3.5 h-3.5" />
        </div>

        {/* Step number badge */}
        <div className={`
          w-7 h-7 rounded-full flex items-center justify-center shrink-0 text-xs font-bold mt-0.5
          ${isSelected
            ? 'bg-accent-primary text-white'
            : hasDescription
              ? 'bg-accent-geo/15 text-accent-geo'
              : 'bg-bg-tertiary text-text-muted'}
        `}>
          {index + 1}
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-medium text-text-primary truncate">
              {step.title || 'Untitled Step'}
            </span>
            {/* Status badges */}
            <div className="flex items-center gap-1">
              {hasHooks && (
                <span className="w-4 h-4 rounded flex items-center justify-center bg-emerald-500/10" title="Has validation hooks">
                  <Shield className="w-2.5 h-2.5 text-emerald-500" />
                </span>
              )}
              {hasParams && (
                <span className="w-4 h-4 rounded flex items-center justify-center bg-blue-500/10" title="Has parameters">
                  <Settings2 className="w-2.5 h-2.5 text-blue-500" />
                </span>
              )}
            </div>
          </div>

          {hasDescription ? (
            <p className="text-[11px] text-text-muted mt-0.5 line-clamp-2 leading-relaxed">
              {step.description}
            </p>
          ) : (
            <p className="text-[11px] text-text-muted/50 mt-0.5 italic">
              Click to add a description...
            </p>
          )}
        </div>

        {/* Actions (visible on hover) */}
        <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
          <ActionBtn
            icon={<Copy className="w-3 h-3" />}
            title="Duplicate"
            onClick={(e) => { e.stopPropagation(); onDuplicate() }}
          />
          <ActionBtn
            icon={<Trash2 className="w-3 h-3" />}
            title="Remove"
            onClick={(e) => { e.stopPropagation(); onRemove() }}
            danger
          />
        </div>
      </div>
    </div>
  )
}

function ActionBtn({
  icon,
  title,
  onClick,
  danger,
}: {
  icon: React.ReactNode
  title: string
  onClick: (e: React.MouseEvent) => void
  danger?: boolean
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`w-5 h-5 rounded flex items-center justify-center transition-colors ${
        danger
          ? 'text-text-muted hover:text-accent-danger hover:bg-accent-danger/10'
          : 'text-text-muted hover:text-text-primary hover:bg-bg-hover'
      }`}
    >
      {icon}
    </button>
  )
}

// ─── Step Inspector (right panel) ────────────────────────────────

interface StepInspectorProps {
  step: WorkflowNode
  stepIndex: number
  totalSteps: number
  onUpdate: (patch: Partial<WorkflowNode>) => void
  onRemove: () => void
}

function StepInspector({ step, stepIndex, totalSteps, onUpdate, onRemove }: StepInspectorProps) {
  const t = useT()
  const [showHooks, setShowHooks] = useState(false)
  const [showParams, setShowParams] = useState(false)

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="h-9 border-b border-border flex items-center px-3 shrink-0">
        <span className="text-xs font-semibold text-text-secondary flex-1 truncate">
          {t.workflow.stepOf.replace('{index}', String(stepIndex + 1)).replace('{total}', String(totalSteps))}
        </span>
        <button
          onClick={onRemove}
          className="w-6 h-6 rounded flex items-center justify-center text-text-muted hover:text-accent-danger hover:bg-accent-danger/10 transition-colors"
          title={t.workflow.deleteStep}
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin p-3 space-y-4">
        {/* Title */}
        <Field label={t.workflow.inspector.stepTitle} hint={t.workflow.inspector.stepTitleHint}>
          <input
            value={step.title}
            onChange={(e) => onUpdate({ title: e.target.value })}
            placeholder={t.workflow.inspector.stepTitlePlaceholder}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-2.5 py-1.5 text-xs text-text-primary outline-none focus:border-accent-primary transition-colors"
          />
        </Field>

        {/* Description — the main instruction for the LLM */}
        <Field
          label={t.workflow.inspector.description}
          hint={t.workflow.inspector.descriptionHint}
        >
          <textarea
            value={step.description ?? ''}
            onChange={(e) => onUpdate({ description: e.target.value })}
            placeholder={t.workflow.inspector.descriptionPlaceholder}
            rows={5}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-2.5 py-1.5 text-xs text-text-primary outline-none focus:border-accent-primary transition-colors resize-y leading-relaxed"
          />
        </Field>

        {/* Max retries */}
        <Field label={t.workflow.inspector.maxRetries} hint={t.workflow.inspector.maxRetriesHint}>
          <input
            type="number"
            min={1}
            max={10}
            value={step.maxRetries ?? 3}
            onChange={(e) => onUpdate({ maxRetries: parseInt(e.target.value) || 3 })}
            className="w-20 bg-bg-tertiary border border-border rounded-lg px-2.5 py-1.5 text-xs text-text-primary outline-none focus:border-accent-primary transition-colors"
          />
        </Field>

        {/* Validation Hooks */}
        <CollapsibleSection
          title={t.workflow.hooks.title}
          icon={<Shield className="w-3.5 h-3.5 text-emerald-500" />}
          count={step.hooks?.length ?? 0}
          open={showHooks}
          onToggle={() => setShowHooks(!showHooks)}
        >
          <HookEditor
            hooks={step.hooks ?? []}
            onChange={(hooks) => onUpdate({ hooks })}
          />
        </CollapsibleSection>

        {/* Parameters */}
        <CollapsibleSection
          title={t.workflow.params.title}
          icon={<Settings2 className="w-3.5 h-3.5 text-blue-500" />}
          count={Object.keys(step.params || {}).length}
          open={showParams}
          onToggle={() => setShowParams(!showParams)}
        >
          <ParamsEditor
            params={step.params || {}}
            onChange={(params) => onUpdate({ params })}
          />
        </CollapsibleSection>

        {/* Notes */}
        <Field label={t.workflow.inspector.notes} hint={t.workflow.inspector.notesHint}>
          <textarea
            value={step.notes ?? ''}
            onChange={(e) => onUpdate({ notes: e.target.value || undefined })}
            rows={2}
            placeholder={t.workflow.inspector.notesPlaceholder}
            className="w-full bg-bg-tertiary border border-border rounded-lg px-2.5 py-1.5 text-xs text-text-primary outline-none focus:border-accent-primary transition-colors resize-y"
          />
        </Field>

        <div className="pt-2 border-t border-border text-2xs text-text-muted font-mono break-all">
          id: {step.id}
        </div>
      </div>
    </div>
  )
}

// ─── Workflow Overview (when no step selected) ───────────────────

interface WorkflowOverviewProps {
  path: string
  doc: { name: string; description?: string; nodes: WorkflowNode[]; edges: WorkflowEdge[] }
  steps: WorkflowNode[]
  onUpdateDoc: (patch: Record<string, any>) => void
}

function WorkflowOverview({ path, steps }: WorkflowOverviewProps) {
  const t = useT()
  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="h-9 border-b border-border flex items-center px-3 shrink-0">
        <span className="text-xs font-semibold text-text-secondary">
          {t.workflow.overview}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto scrollbar-thin p-3 space-y-4">
        {/* Pipeline preview */}
        <div className="space-y-1">
          <label className="text-2xs text-text-muted uppercase tracking-wider font-medium">
            {t.workflow.pipeline}
          </label>
          <div className="bg-bg-tertiary rounded-lg p-3 space-y-1.5">
            {steps.length === 0 ? (
              <p className="text-2xs text-text-muted/60 italic text-center py-2">
                {t.workflow.noStepsDefined}
              </p>
            ) : (
              steps.map((step, i) => (
                <div key={step.id}>
                  <div className="flex items-center gap-2">
                    <div className={`w-5 h-5 rounded-full flex items-center justify-center text-2xs font-bold ${
                      step.description?.trim()
                        ? 'bg-accent-geo/15 text-accent-geo'
                        : 'bg-bg-primary text-text-muted'
                    }`}>
                      {i + 1}
                    </div>
                    <span className="text-2xs text-text-secondary truncate flex-1">
                      {step.title || 'Untitled'}
                    </span>
                    {step.description?.trim() ? (
                      <CheckCircle2 className="w-3 h-3 text-emerald-500 shrink-0" />
                    ) : (
                      <Circle className="w-3 h-3 text-text-muted/30 shrink-0" />
                    )}
                  </div>
                  {i < steps.length - 1 && (
                    <div className="ml-2.5 border-l border-border h-1.5" />
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* Stats */}
        <div className="space-y-1">
          <label className="text-2xs text-text-muted uppercase tracking-wider font-medium">
            {t.workflow.statistics}
          </label>
          <div className="grid grid-cols-2 gap-2">
            <StatCard label={t.workflow.stats.steps} value={steps.length} />
            <StatCard
              label={t.workflow.stats.withHooks}
              value={steps.filter((s) => (s.hooks?.length ?? 0) > 0).length}
            />
            <StatCard
              label={t.workflow.stats.configured}
              value={steps.filter((s) => !!s.description?.trim()).length}
            />
            <StatCard
              label={t.workflow.stats.withParams}
              value={steps.filter((s) => Object.keys(s.params || {}).length > 0).length}
            />
          </div>
        </div>

        {/* Info */}
        <div className="pt-2 border-t border-border space-y-1 text-2xs text-text-muted">
          <div className="font-mono break-all">Path: {path}</div>
        </div>

        <div className="pt-2 text-2xs text-text-muted italic">
          {t.workflow.selectStepHint}
        </div>
      </div>
    </div>
  )
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-bg-tertiary rounded-lg px-3 py-2 text-center">
      <div className="text-base font-bold text-text-primary">{value}</div>
      <div className="text-2xs text-text-muted">{label}</div>
    </div>
  )
}

// ─── Hook Editor ─────────────────────────────────────────────────

function HookEditor({
  hooks,
  onChange,
}: {
  hooks: StepHook[]
  onChange: (hooks: StepHook[]) => void
}) {
  const t = useT()
  const addHook = () => {
    onChange([...hooks, { expression: '', description: '', onFail: 'retry' }])
  }

  const updateHook = (i: number, patch: Partial<StepHook>) => {
    const next = [...hooks]
    next[i] = { ...next[i], ...patch }
    onChange(next)
  }

  const removeHook = (i: number) => {
    onChange(hooks.filter((_, j) => j !== i))
  }

  return (
    <div className="space-y-2">
      <p className="text-2xs text-text-muted/70 leading-relaxed">
        {t.workflow.hooks.description}
      </p>

      {hooks.map((hook, i) => (
        <div key={i} className="bg-bg-primary rounded-lg p-2.5 space-y-1.5 border border-border/50">
          <div className="flex items-start gap-1.5">
            <input
              value={hook.expression}
              onChange={(e) => updateHook(i, { expression: e.target.value })}
              placeholder={t.workflow.hooks.expressionPlaceholder}
              className="flex-1 bg-bg-tertiary border border-border rounded px-2 py-1 text-2xs text-text-primary outline-none focus:border-accent-primary font-mono"
            />
            <button
              onClick={() => removeHook(i)}
              className="w-5 h-5 rounded flex items-center justify-center text-text-muted hover:text-accent-danger hover:bg-accent-danger/10 transition-colors shrink-0"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
          <div className="flex items-center gap-2">
            <input
              value={hook.description ?? ''}
              onChange={(e) => updateHook(i, { description: e.target.value })}
              placeholder={t.workflow.hooks.descriptionPlaceholder}
              className="flex-1 bg-bg-tertiary border border-border rounded px-2 py-1 text-2xs text-text-secondary outline-none focus:border-accent-primary"
            />
            <select
              value={hook.onFail ?? 'retry'}
              onChange={(e) => updateHook(i, { onFail: e.target.value as StepHook['onFail'] })}
              className="bg-bg-tertiary border border-border rounded px-1.5 py-1 text-2xs text-text-secondary outline-none"
            >
              <option value="retry">{t.workflow.hooks.onFailRetry}</option>
              <option value="ask_user">{t.workflow.hooks.onFailAskUser}</option>
              <option value="skip">{t.workflow.hooks.onFailSkip}</option>
            </select>
          </div>
        </div>
      ))}

      <button
        onClick={addHook}
        className="w-full text-2xs text-text-muted hover:text-accent-primary border border-dashed border-border hover:border-accent-primary/50 rounded-lg py-1.5 transition-colors flex items-center justify-center gap-1"
      >
        <Plus className="w-3 h-3" />
        {t.workflow.hooks.addHook}
      </button>
    </div>
  )
}

// ─── Params Editor ───────────────────────────────────────────────

function ParamsEditor({
  params,
  onChange,
}: {
  params: Record<string, unknown>
  onChange: (params: Record<string, unknown>) => void
}) {
  const t = useT()
  const entries = Object.entries(params)

  const addParam = () => {
    const key = `param_${entries.length + 1}`
    onChange({ ...params, [key]: '' })
  }

  const updateKey = (oldKey: string, newKey: string) => {
    if (newKey === oldKey) return
    const newParams: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(params)) {
      newParams[k === oldKey ? newKey : k] = v
    }
    onChange(newParams)
  }

  const updateValue = (key: string, value: string) => {
    // Try to parse as JSON, fall back to string
    let parsed: unknown = value
    try {
      parsed = JSON.parse(value)
    } catch {
      parsed = value
    }
    onChange({ ...params, [key]: parsed })
  }

  const removeParam = (key: string) => {
    const next = { ...params }
    delete next[key]
    onChange(next)
  }

  return (
    <div className="space-y-2">
      <p className="text-2xs text-text-muted/70 leading-relaxed">
        {t.workflow.params.description}
      </p>

      {entries.map(([key, value]) => (
        <div key={key} className="flex items-center gap-1.5">
          <input
            value={key}
            onChange={(e) => updateKey(key, e.target.value)}
            placeholder={t.workflow.params.keyPlaceholder}
            className="w-24 bg-bg-tertiary border border-border rounded px-2 py-1 text-2xs text-text-primary outline-none focus:border-accent-primary font-mono"
          />
          <input
            value={typeof value === 'string' ? value : JSON.stringify(value)}
            onChange={(e) => updateValue(key, e.target.value)}
            placeholder={t.workflow.params.valuePlaceholder}
            className="flex-1 bg-bg-tertiary border border-border rounded px-2 py-1 text-2xs text-text-primary outline-none focus:border-accent-primary"
          />
          <button
            onClick={() => removeParam(key)}
            className="w-5 h-5 rounded flex items-center justify-center text-text-muted hover:text-accent-danger hover:bg-accent-danger/10 transition-colors shrink-0"
          >
            <X className="w-3 h-3" />
          </button>
        </div>
      ))}

      <button
        onClick={addParam}
        className="w-full text-2xs text-text-muted hover:text-accent-primary border border-dashed border-border hover:border-accent-primary/50 rounded-lg py-1.5 transition-colors flex items-center justify-center gap-1"
      >
        <Plus className="w-3 h-3" />
        {t.workflow.params.addParam}
      </button>
    </div>
  )
}

// ─── Shared UI components ────────────────────────────────────────

function ToolbarButton({
  icon,
  label,
  onClick,
  disabled,
  accent,
}: {
  icon: React.ReactNode
  label: string
  onClick: () => void
  disabled?: boolean
  accent?: boolean
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`
        h-7 px-2.5 rounded flex items-center gap-1.5 text-xs transition-colors
        ${disabled
          ? 'text-text-muted/40 cursor-not-allowed'
          : accent
            ? 'text-accent-primary hover:bg-accent-primary/10'
            : 'text-text-secondary hover:text-text-primary hover:bg-bg-hover'}
      `}
    >
      {icon}
      <span>{label}</span>
    </button>
  )
}

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1">
      <label className="text-2xs text-text-muted uppercase tracking-wider font-medium">
        {label}
      </label>
      {children}
      {hint && <p className="text-2xs text-text-muted/70 italic leading-relaxed">{hint}</p>}
    </div>
  )
}

function CollapsibleSection({
  title,
  icon,
  count,
  open,
  onToggle,
  children,
}: {
  title: string
  icon: React.ReactNode
  count: number
  open: boolean
  onToggle: () => void
  children: React.ReactNode
}) {
  return (
    <div className="border border-border rounded-lg overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-3 py-2 text-xs text-text-secondary hover:text-text-primary hover:bg-bg-hover transition-colors"
      >
        {open ? (
          <ChevronDown className="w-3 h-3 text-text-muted" />
        ) : (
          <ChevronRight className="w-3 h-3 text-text-muted" />
        )}
        {icon}
        <span className="flex-1 text-left font-medium">{title}</span>
        {count > 0 && (
          <span className="text-2xs bg-accent-primary/10 text-accent-primary px-1.5 py-0.5 rounded-full">
            {count}
          </span>
        )}
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1 border-t border-border/50">
          {children}
        </div>
      )}
    </div>
  )
}

function EmptyWorkflowState({ onAddStep }: { onAddStep: () => void }) {
  const t = useT()
  return (
    <div className="flex-1 flex items-center justify-center p-8 h-full">
      <div className="text-center max-w-xs">
        <div className="w-14 h-14 rounded-2xl bg-accent-geo/10 flex items-center justify-center mx-auto mb-4">
          <Zap className="w-7 h-7 text-accent-geo/60" />
        </div>
        <h3 className="text-sm font-semibold text-text-primary mb-1.5">
          {t.workflow.buildTitle}
        </h3>
        <p className="text-xs text-text-muted leading-relaxed mb-4">
          {t.workflow.buildDescription}
        </p>

        <div className="space-y-2 text-left mb-5">
          <GuideItem
            num={1}
            text={t.workflow.guide1}
          />
          <GuideItem
            num={2}
            text={t.workflow.guide2}
          />
          <GuideItem
            num={3}
            text={t.workflow.guide3}
          />
        </div>

        <button
          onClick={onAddStep}
          className="inline-flex items-center gap-2 px-4 py-2 bg-accent-primary text-white text-xs font-medium rounded-lg hover:bg-accent-primary/90 transition-colors shadow-sm"
        >
          <Plus className="w-3.5 h-3.5" />
          {t.workflow.addFirstStep}
        </button>
      </div>
    </div>
  )
}

function GuideItem({ num, text }: { num: number; text: string }) {
  return (
    <div className="flex items-start gap-2.5">
      <div className="w-5 h-5 rounded-full bg-accent-primary/10 flex items-center justify-center shrink-0 mt-0.5">
        <span className="text-2xs font-bold text-accent-primary">{num}</span>
      </div>
      <span className="text-2xs text-text-secondary leading-relaxed">{text}</span>
    </div>
  )
}
