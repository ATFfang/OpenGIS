import { memo, useState, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { Copy, Check, X, Download } from 'lucide-react'
import { useChatCodeTheme } from './useChatCodeTheme'
import { pathToImageUrl, releaseImageUrl } from '@/services/rpc/handlers/_image_url'
import 'katex/dist/katex.min.css'

interface MarkdownBlockProps {
  markdown?: string
  showCursor?: boolean
  /** Directory of the markdown source file. Relative images resolve here. */
  baseDir?: string
  /** Optional: resolve a relative image path to a usable URL (async). */
  resolveImageSrc?: (relativePath: string) => Promise<string>
}

const URL_LIKE_RE = /^(https?:|data:|blob:)/i
const WINDOWS_ABS_RE = /^[a-zA-Z]:[\\/]/
const IMAGE_PATH_RE = /\.(png|jpe?g|gif|bmp|webp|svg)(?:[?#].*)?$/i

function stripFileProtocol(src: string): string {
  if (!src.toLowerCase().startsWith('file://')) return src
  try {
    return decodeURIComponent(new URL(src).pathname)
  } catch {
    return src.replace(/^file:\/\//i, '')
  }
}

function decodeLocalPath(src: string): string {
  try {
    return decodeURIComponent(src)
  } catch {
    return src
  }
}

function joinBasePath(baseDir: string, relativePath: string): string {
  const root = baseDir.replace(/[\\/]+$/, '')
  const rel = relativePath.replace(/^\.?[\\/]+/, '')
  return `${root}/${rel}`
}

function localImagePathFor(src: string, baseDir?: string): string | null {
  if (URL_LIKE_RE.test(src)) return null
  const localPath = decodeLocalPath(stripFileProtocol(src).trim())
  const isAbsoluteLocal = localPath.startsWith('/') || WINDOWS_ABS_RE.test(localPath)
  if (isAbsoluteLocal && IMAGE_PATH_RE.test(localPath)) {
    return localPath
  }

  if (baseDir && IMAGE_PATH_RE.test(localPath)) {
    return joinBasePath(baseDir, localPath)
  }

  return null
}

async function defaultResolveImageSrc(src: string, baseDir?: string): Promise<string> {
  const localPath = localImagePathFor(src, baseDir)
  return localPath ? pathToImageUrl(localPath) : src
}

/**
 * ResolvedImage — async image component for local file paths.
 * Uses resolveImageSrc to convert a relative path to a Blob URL via
 * Electron IPC, then renders the image.
 */
function ResolvedImage({ src, alt, resolveImageSrc, releasePath, onClick }: {
  src: string
  alt?: string
  resolveImageSrc: (path: string) => Promise<string>
  releasePath?: string | null
  onClick?: (resolvedSrc: string) => void
}) {
  const [resolvedSrc, setResolvedSrc] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let acquired = false
    setResolvedSrc(null)
    setError(null)
    resolveImageSrc(src).then((url) => {
      acquired = true
      if (cancelled) {
        if (releasePath) releaseImageUrl(releasePath)
        return
      }
      setResolvedSrc(url)
    }).catch((err) => {
      if (!cancelled) setError((err as Error)?.message || 'Image could not be loaded')
    })
    return () => {
      cancelled = true
      if (acquired && releasePath) releaseImageUrl(releasePath)
    }
  }, [src, resolveImageSrc, releasePath])

  if (error) {
    return (
      <div className="my-2 rounded-lg border border-border/15 bg-bg-tertiary/40 px-3 py-2 text-xs text-text-muted">
        Image unavailable: {src}
      </div>
    )
  }

  if (!resolvedSrc) {
    return (
      <div className="my-2 flex w-full justify-center">
        <div className="h-20 w-full max-w-[min(560px,100%)] rounded-lg bg-bg-tertiary/30 animate-pulse" />
      </div>
    )
  }

  return (
    <span className="my-2 flex w-full justify-center">
      <img
        src={resolvedSrc}
        alt={alt}
        className="block h-auto max-h-[360px] w-auto max-w-[min(560px,100%)] rounded-lg cursor-zoom-in object-contain"
        onClick={() => onClick?.(resolvedSrc)}
        onError={(e) => {
          // Fallback: hide the image if it fails to load
          ;(e.target as HTMLImageElement).style.display = 'none'
        }}
      />
    </span>
  )
}

const MarkdownBlock = memo(({ markdown, showCursor, baseDir, resolveImageSrc }: MarkdownBlockProps) => {
  const defaultImageResolver = useCallback(
    (src: string) => defaultResolveImageSrc(src, baseDir),
    [baseDir]
  )
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null)

  if (!markdown) return null

  // NOTE: 之前这里用 `<span className="inline">` 包住 ReactMarkdown，但 react-markdown
  // 会把 fenced code block 渲染成 <div>（我们的 CodeBlock）。把 block-level <div> 放进
  // inline <span> 是无效 HTML，浏览器会自动"修复"，在实际 DOM 里提前关闭 span 再插 div，
  // 结果经常看到**双边距 / 边框错位 / 代码块"重影"**。改成 block-level <div> 就彻底没事。
  return (
    <div className="inline-markdown-block w-full min-w-0 overflow-hidden break-words select-text">
      <div className={`[&>p:first-child]:mt-0 ${showCursor ? 'inline-cursor-container' : ''}`}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={[rehypeKatex]}
          components={{
            img({ src, alt, ...props }) {
              const handleClick = (resolvedSrc: string) => {
                setLightboxSrc(resolvedSrc)
              }
              if (src && !URL_LIKE_RE.test(src)) {
                const releasePath = resolveImageSrc ? null : localImagePathFor(src, baseDir)
                return (
                  <ResolvedImage
                    src={src}
                    alt={alt}
                    resolveImageSrc={resolveImageSrc ?? defaultImageResolver}
                    releasePath={releasePath}
                    onClick={handleClick}
                  />
                )
              }
              return (
                <span className="my-2 flex w-full justify-center">
                  <img
                    src={src}
                    alt={alt}
                    className="block h-auto max-h-[360px] w-auto max-w-[min(560px,100%)] rounded-lg cursor-zoom-in object-contain"
                    onClick={() => src && handleClick(src)}
                    {...props}
                  />
                </span>
              )
            },
            code({ className, children, node, ...props }) {
              const match = /language-(\w+)/.exec(className || '')
              const codeString = String(children).replace(/\n$/, '')

              // Determine if this is a block code (inside <pre>) or inline code.
              // react-markdown wraps fenced code blocks in <pre><code>, so we check
              // the parent element to distinguish block vs inline.
              const isBlockCode = node?.position?.start.line !== node?.position?.end.line ||
                (node as any)?.parent?.tagName === 'pre' ||
                codeString.includes('\n')

              // Block code (with or without language)
              if (isBlockCode) {
                if (showCursor) {
                  return <StreamingCodeBlock language={match ? match[1] : 'text'} code={codeString} />
                }
                return (
                  <CodeBlock language={match ? match[1] : 'text'} code={codeString} />
                )
              }

              // Inline code
              return (
                <code
                  className="font-mono text-[12px] bg-[var(--bg-tertiary)] rounded-[4px] px-1.5 py-0.5 whitespace-pre-line break-words text-accent-primary/90"
                  {...props}
                >
                  {children}
                </code>
              )
            },
            pre({ children }) {
              return <>{children}</>
            },
            p({ children }) {
              return <p className="my-2 leading-[1.75] text-[13px] text-text-primary/90">{children}</p>
            },
            ul({ children }) {
              return <ul className="my-2 ml-5 list-disc space-y-0.5">{children}</ul>
            },
            ol({ children }) {
              return <ol className="my-2 ml-5 list-decimal space-y-0.5">{children}</ol>
            },
            li({ children }) {
              return <li className="leading-[1.7] text-[13px]">{children}</li>
            },
            a({ href, children }) {
              return (
                <a
                  href={href}
                  className="text-accent-primary hover:text-accent-primary/80 underline underline-offset-2 decoration-accent-primary/30 hover:decoration-accent-primary/60 transition-colors"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {children}
                </a>
              )
            },
            table({ children }) {
              return (
                <div className="overflow-x-auto my-3 rounded-lg">
                  <table className="border-collapse w-full text-[13px]">
                    {children}
                  </table>
                </div>
              )
            },
            th({ children }) {
              return (
                <th className="p-2.5 border-b border-border/20 text-left bg-bg-tertiary/50 font-semibold text-text-primary text-[12px] uppercase tracking-wider">
                  {children}
                </th>
              )
            },
            td({ children }) {
              return (
                <td className="p-2.5 border-b border-border/15 text-left text-text-secondary">
                  {children}
                </td>
              )
            },
            hr() {
              return <hr className="my-4 border-border/15" />
            },
            blockquote({ children }) {
              return (
                <blockquote className="border-l-[3px] border-accent-primary/30 pl-3.5 my-3 text-text-secondary/80 italic">
                  {children}
                </blockquote>
              )
            },
            h1({ children }) {
              return <h1 className="text-lg font-bold mt-5 mb-2 text-text-primary">{children}</h1>
            },
            h2({ children }) {
              return <h2 className="text-base font-bold mt-4 mb-2 text-text-primary">{children}</h2>
            },
            h3({ children }) {
              return <h3 className="text-[14px] font-semibold mt-3 mb-1.5 text-text-primary">{children}</h3>
            },
            strong({ children }) {
              return <strong className="font-semibold text-text-primary">{children}</strong>
            },
            em({ children }) {
              return <em className="italic text-text-secondary">{children}</em>
            },
          }}
        >
          {markdown}
        </ReactMarkdown>
      </div>

      {/* Image Lightbox */}
      {lightboxSrc && (
        <div
          className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/80 backdrop-blur-sm"
          onClick={() => setLightboxSrc(null)}
        >
          <div className="relative max-w-[90vw] max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
            <img
              src={lightboxSrc}
              alt="Preview"
              className="max-w-full max-h-[90vh] object-contain rounded-lg shadow-2xl"
            />
            <div className="absolute top-3 right-3 flex gap-2">
              <a
                href={lightboxSrc}
                download
                className="w-8 h-8 rounded-full bg-black/50 hover:bg-black/70 flex items-center justify-center text-white transition-colors"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-4 h-4" />
              </a>
              <button
                onClick={() => setLightboxSrc(null)}
                className="w-8 h-8 rounded-full bg-black/50 hover:bg-black/70 flex items-center justify-center text-white transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
})

MarkdownBlock.displayName = 'MarkdownBlock'

export default MarkdownBlock

// --- Code Block with copy button ---

function StreamingCodeBlock({ language, code }: { language: string; code: string }) {
  return (
    <div className="relative my-2 overflow-hidden rounded-lg border border-border/10 bg-bg-secondary/55">
      <div className="flex items-center justify-between border-b border-border/10 bg-bg-tertiary/50 px-3 py-1.5">
        <span className="font-mono text-[10px] uppercase tracking-wider text-text-muted">{language}</span>
      </div>
      <pre className="m-0 overflow-x-auto whitespace-pre-wrap break-words bg-bg-tertiary/55 px-3.5 py-3 font-mono text-[11.75px] leading-[1.58] text-text-secondary">
        <code>{code || ' '}</code>
      </pre>
    </div>
  )
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false)
  const { style: codeTheme } = useChatCodeTheme()

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [code])

  return (
    <div className="relative group my-2 rounded-lg overflow-hidden border border-border/10 bg-bg-secondary/55">
      {/* Language badge + copy button */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-bg-tertiary/50 border-b border-border/10">
        <span className="text-[10px] font-mono text-text-muted uppercase tracking-wider">{language}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded text-text-muted hover:text-text-primary hover:bg-bg-hover transition-all duration-150"
        >
          {copied ? (
            <>
              <Check className="w-3 h-3 text-accent-success" />
              <span className="text-accent-success">Copied</span>
            </>
          ) : (
            <>
              <Copy className="w-3 h-3" />
              <span>Copy</span>
            </>
          )}
        </button>
      </div>
      <SyntaxHighlighter
        style={codeTheme}
        language={language}
        PreTag="div"
        customStyle={{
          margin: 0,
          borderRadius: 0,
          fontSize: '11.75px',
          lineHeight: '1.58',
          padding: '12px 14px',
          background: 'color-mix(in srgb, var(--bg-tertiary) 58%, transparent)',
          border: 'none',
          textShadow: 'none',
        }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  )
}
