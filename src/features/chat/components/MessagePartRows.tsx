import { AlertTriangle, Hourglass } from 'lucide-react'
import { useChatStore } from '@/stores/chatStore'
import { useT } from '@/i18n'
export function dirname(path: string): string {
  const normalized = path.replace(/\\/g, '/')
  const idx = normalized.lastIndexOf('/')
  return idx > 0 ? normalized.slice(0, idx) : ''
}

export function markdownBaseDirFor(baseDir?: string, files?: string[]): string | undefined {
  if (baseDir) return baseDir
  const mdFile = files?.find((file) => /\.md$/i.test(file))
  if (mdFile) return dirname(mdFile)
  return undefined
}

export function UserMessageRow({
  text,
  files,
  images,
}: {
  text: string
  files?: string[]
  images?: string[]
}) {
  return (
    <div
      className="px-4 py-2.5 rounded-2xl rounded-tr-sm text-[13px] shadow-sm bg-accent-primary/15 border border-accent-primary/25"
      style={{
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        overflowWrap: 'break-word',
        maxWidth: '100%',
      }}
    >
      <span className="block text-text-primary leading-relaxed">{text}</span>
      {files && files.length > 0 && (
        <div className="flex gap-1.5 mt-2 flex-wrap">
          {files.map((file, index) => (
            <span
              key={index}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-bg-tertiary/80 border border-border text-[10px] text-text-muted"
            >
              📎 {file}
            </span>
          ))}
        </div>
      )}
      {images && images.length > 0 && (
        <div className="flex gap-2 mt-2.5 flex-wrap">
          {images.map((image, index) => (
            <img
              key={index}
              src={image}
              alt=""
              className="w-16 h-16 object-cover rounded-lg border border-border shadow-sm"
            />
          ))}
        </div>
      )}
    </div>
  )
}

export function ErrorRow({ text }: { text?: string }) {
  const errorText = text || 'Unknown error'
  const mainText = errorText.startsWith('⚠️') ? errorText : `⚠️ ${errorText}`

  return (
    <div className="text-[13px] bg-accent-danger/5 border border-accent-danger/15 rounded-xl px-4 py-3 shadow-sm">
      <div className="flex items-start gap-2.5">
        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5 text-accent-danger" />
        <div className="flex-1 min-w-0">
          <span className="whitespace-pre-wrap break-words leading-relaxed text-text-primary">
            {mainText}
          </span>
        </div>
      </div>
    </div>
  )
}

export function ProgressRow({
  stage = 'processing',
  detail,
}: {
  stage?: string
  detail?: string
}) {
  const labels = useProgressLabels()
  const label = labels[stage] || labels.processing

  return (
    <div className="flex items-center gap-2.5 py-1.5 animate-fade-in">
      <div className="relative w-4 h-4">
        <div className="absolute inset-0 rounded-full border-2 border-accent-primary/20" />
        <div className="absolute inset-0 rounded-full border-2 border-accent-primary border-t-transparent animate-spin" />
      </div>
      <span className="text-[12px] text-text-muted font-medium">
        {detail || label}
      </span>
    </div>
  )
}

export function MaxStepsReachedRow({
  maxSteps,
  isLast,
}: {
  maxSteps?: number
  isLast: boolean
}) {
  const t = useT()
  const isStreaming = useChatStore((state) => state.isStreaming)
  const sendMessage = useChatStore((state) => state.sendMessage)
  const canContinue = isLast && !isStreaming

  const handleContinue = () => {
    if (canContinue) sendMessage(t.chat.continue)
  }

  return (
    <div className="flex items-start gap-3 bg-bg-tertiary/40 border border-border/30 rounded-xl px-4 py-3">
      <Hourglass className="w-4 h-4 shrink-0 mt-0.5 text-text-muted" />
      <div className="flex-1 min-w-0">
        <div className="text-[13px] text-text-primary leading-relaxed">
          {t.chat.maxStepsReached.replace('{maxSteps}', String(maxSteps ?? '?'))}
        </div>
        <div className="text-[11px] text-text-muted mt-1">
          {t.chat.maxStepsHint}
        </div>
        <div className="mt-2.5 flex gap-2">
          <button
            type="button"
            onClick={handleContinue}
            disabled={!canContinue}
            className="px-3 py-1.5 rounded-md text-[12px] font-medium bg-accent-primary/10 hover:bg-accent-primary/15 text-accent-primary border border-accent-primary/20 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {t.chat.continue}
          </button>
        </div>
      </div>
    </div>
  )
}

function useProgressLabels(): Record<string, string> {
  const t = useT()
  return {
    installing_packages: `📦 ${t.chat.progressInstalling}`,
    loading_geodata: `🗺️ ${t.chat.progressLoadingGeodata}`,
    loading_raster: `🛰️ ${t.chat.progressLoadingRaster}`,
    loading_data: `📊 ${t.chat.progressLoadingData}`,
    spatial_analysis: `📐 ${t.chat.progressSpatialAnalysis}`,
    generating_visualization: `🎨 ${t.chat.progressVisualization}`,
    rendering_map: `🗺️ ${t.chat.progressRendering}`,
    saving_results: `💾 ${t.chat.progressSaving}`,
    executing_code: `⚙️ ${t.chat.progressExecuting}`,
    processing: `⏳ ${t.chat.progressProcessing}`,
  }
}
