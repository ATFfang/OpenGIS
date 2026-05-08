import { useEffect, useState, useCallback } from 'react'
import { ChevronDown, ChevronRight, Zap, Layers, Search, Code, FileText, Map, HelpCircle } from 'lucide-react'
import { pythonClient } from '@/services/pythonClient'
import { useT } from '@/i18n'

// ─── Types matching SkillSchema.to_dict() / SkillParam.to_dict() ───
interface SkillParamDict {
  name: string
  type: string
  description: string
  required: boolean
  default?: unknown
  options?: string[]
}

interface SkillSchemaDict {
  name: string
  display_name: string
  description: string
  category: string
  params: SkillParamDict[]
  returns: string
  examples: string[]
  tags: string[]
  version: string
}

type CategoryInfo = {
  label: string
  icon: typeof Zap
  color: string // tailwind text-/bg- class
  bgColor: string // background color class for category badge
}

const CATEGORY_MAP: Record<string, CategoryInfo> = {
  visualization: { label: 'Visualization', icon: Map, color: 'text-blue-400', bgColor: 'bg-blue-500/10' },
  analysis:     { label: 'Analysis',      icon: Zap, color: 'text-amber-400', bgColor: 'bg-amber-500/10' },
  data:          { label: 'Data',           icon: FileText, color: 'text-emerald-400', bgColor: 'bg-emerald-500/10' },
  utility:       { label: 'Utility',        icon: Code, color: 'text-purple-400', bgColor: 'bg-purple-500/10' },
  gis:           { label: 'GIS',            icon: Layers, color: 'text-cyan-400', bgColor: 'bg-cyan-500/10' },
}

const FALLBACK_CATEGORY: CategoryInfo = {
  label: 'Other',
  icon: HelpCircle,
  color: 'text-text-muted',
  bgColor: 'bg-bg-tertiary/50',
}

// ─── Component ───────────────────────────────────────────────────────
export function SkillsPanel() {
  const [skills, setSkills] = useState<SkillSchemaDict[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [expandedCategory, setExpandedCategory] = useState<string | null>(null)
  const [expandedSkill, setExpandedSkill] = useState<string | null>(null)
  const t = useT()

  const fetchSkills = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const resp = await pythonClient.send('rpc.skill.list', {})
      const list: SkillSchemaDict[] = (resp as any)?.skills ?? []
      setSkills(list)
      // Auto-expand the first category
      const cats = [...new Set(list.map(s => s.category))]
      if (cats.length > 0) setExpandedCategory(cats[0]!)
    } catch (e: any) {
      setError(e?.message ?? t.common.error)
    } finally {
      setLoading(false)
    }
  }, [t.common.error])

  useEffect(() => {
    fetchSkills()
  }, [fetchSkills])

  const filtered = search.trim()
    ? skills.filter(
        s =>
          s.name.toLowerCase().includes(search.toLowerCase()) ||
          s.display_name.toLowerCase().includes(search.toLowerCase()) ||
          s.description.toLowerCase().includes(search.toLowerCase()) ||
          s.tags.some(t => t.toLowerCase().includes(search.toLowerCase())),
      )
    : skills

  const grouped: Record<string, SkillSchemaDict[]> = {}
  for (const s of filtered) {
    const cat = s.category || 'other'
    ;(grouped[cat] ??= []).push(s)
  }

  // ── Loading state ──────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center gap-3 text-text-muted">
        <div className="w-6 h-6 border-2 border-accent-primary border-t-transparent rounded-full animate-spin" />
        <span className="text-xs">{t.skills.loading}</span>
      </div>
    )
  }

  // ── Error state ───────────────────────────────────────────────────
  if (error) {
    return (
      <div className="w-full h-full flex flex-col items-center justify-center gap-3 text-accent-danger p-4">
        <span className="text-xs text-center">{error}</span>
        <button
          onClick={fetchSkills}
          className="text-xs px-3 py-1 rounded bg-accent-primary/20 text-accent-primary hover:bg-accent-primary/30 transition-colors"
        >
          {t.skills.retry}
        </button>
      </div>
    )
  }

  // ── Empty state ───────────────────────────────────────────────────
  if (skills.length === 0) {
    return (
      <div className="w-full h-full flex items-center justify-center text-xs text-text-muted">
        {t.skills.noSkills}
      </div>
    )
  }

  // ── Main render ───────────────────────────────────────────────────
  return (
    <div className="w-full h-full flex flex-col overflow-hidden bg-bg-primary">
      {/* Header + search */}
      <div className="px-3 py-2 border-b border-border">
        <div className="flex items-center gap-2 mb-2">
          <Zap className="w-4 h-4 text-accent-primary" />
          <span className="text-xs font-semibold text-text-primary">{t.skills.title}</span>
          <span className="ml-auto text-[10px] text-text-muted">{skills.length}</span>
        </div>
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-muted" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={t.skills.searchPlaceholder}
            className="w-full pl-7 pr-2 py-1.5 text-xs bg-bg-secondary border border-border rounded-md
                       text-text-primary placeholder-text-muted
                       focus:outline-none focus:border-accent-primary/50"
          />
        </div>
      </div>

      {/* Skill list */}
      <div className="flex-1 overflow-y-auto">
        {Object.entries(grouped).map(([cat, items]) => {
          const info = CATEGORY_MAP[cat] ?? FALLBACK_CATEGORY
          const Icon = info.icon
          const isOpen = expandedCategory === cat
          const categoryLabel = t.skills.categories[cat as keyof typeof t.skills.categories] ?? info.label
          return (
            <div key={cat} className="border-b border-border/50 last:border-b-0">
              {/* Category header */}
              <button
                onClick={() => setExpandedCategory(isOpen ? null : cat)}
                className="w-full flex items-center gap-2 px-3 py-1.5 text-left
                           hover:bg-bg-hover transition-colors"
              >
                {isOpen ? (
                  <ChevronDown className="w-3.5 h-3.5 text-text-muted" />
                ) : (
                  <ChevronRight className="w-3.5 h-3.5 text-text-muted" />
                )}
                <Icon className={`w-3.5 h-3.5 ${info.color}`} />
                <span className="text-[11px] font-medium text-text-secondary">{categoryLabel}</span>
                <span className="ml-auto text-[10px] text-text-muted">{items.length}</span>
              </button>

              {/* Skills in this category */}
              {isOpen && (
                <div className="pb-1">
                  {items.map(skill => {
                    const isSkillOpen = expandedSkill === skill.name
                    return (
                      <div
                        key={skill.name}
                        className="mx-2 mb-0.5"
                      >
                        <button
                          onClick={() =>
                            setExpandedSkill(isSkillOpen ? null : skill.name)
                          }
                          className="w-full flex items-center gap-2 px-2 py-1 text-left
                                     hover:bg-bg-hover transition-colors rounded-md"
                        >
                          {isSkillOpen ? (
                            <ChevronDown className="w-3 h-3 text-accent-primary" />
                          ) : (
                            <ChevronRight className="w-3 h-3 text-text-muted" />
                          )}
                          <span className="text-[11px] font-mono text-accent-primary truncate flex-1">
                            {skill.name}
                          </span>
                          {skill.tags.slice(0, 1).map(t => (
                            <span
                              key={t}
                              className="text-[9px] px-1 py-0.5 rounded bg-accent-primary/10 text-accent-primary/80"
                            >
                              {t}
                            </span>
                          ))}
                        </button>

                        {/* Expanded skill detail */}
                        {isSkillOpen && (
                          <div className="px-3 py-2 text-xs text-text-secondary space-y-2
                                      border-t border-border/50">
                            <p className="leading-relaxed text-[12px]">{skill.description}</p>

                            {/* Params */}
                            {skill.params.length > 0 && (
                              <div>
                                <div className="mb-1 text-[11px] font-semibold text-text-primary uppercase tracking-wide">{t.skills.params}</div>
                                <div className="space-y-1">
                                  {skill.params.map(p => (
                                    <div key={p.name} className="flex items-start gap-2">
                                      <span className="font-mono text-[10px] text-accent-primary bg-accent-primary/10 px-1 py-0.5 rounded whitespace-nowrap">
                                        {p.name}
                                        {p.required && <span className="text-accent-danger">*</span>}
                                      </span>
                                      <span className="text-[10px] text-text-muted leading-tight flex-1">{p.description}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            )}

                            {/* Returns */}
                            {skill.returns && (
                              <div>
                                <div className="mb-0.5 text-[11px] font-semibold text-text-primary uppercase tracking-wide">{t.skills.returns}</div>
                                <p className="text-[11px] text-text-muted">{skill.returns}</p>
                              </div>
                            )}

                            {/* Examples */}
                            {skill.examples.length > 0 && (
                              <div>
                                <div className="mb-0.5 text-[11px] font-semibold text-text-primary uppercase tracking-wide">{t.skills.examples}</div>
                                <ul className="list-disc list-inside space-y-0.5">
                                  {skill.examples.map((ex, i) => (
                                    <li key={i} className="text-[10px] text-text-muted leading-relaxed">{ex}</li>
                                  ))}
                                </ul>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
