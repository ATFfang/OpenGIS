import { memo, useState, useEffect, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { Copy, Check } from 'lucide-react'
import { useChatCodeTheme } from './useChatCodeTheme'

interface MarkdownBlockProps {
  markdown?: string
  showCursor?: boolean
  /** Optional: resolve a relative image path to a usable URL (async). */
  resolveImageSrc?: (relativePath: string) => Promise<string>
}

/**
 * ResolvedImage — async image component for local file paths.
 * Uses resolveImageSrc to convert a relative path to a Blob URL via
 * Electron IPC, then renders the image.
 */
function ResolvedImage({ src, alt, resolveImageSrc }: {
  src: string
  alt?: string
  resolveImageSrc: (path: string) => Promise<string>
}) {
  const [resolvedSrc, setResolvedSrc] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    resolveImageSrc(src).then((url) => {
      if (!cancelled) setResolvedSrc(url)
    }).catch(() => {
      if (!cancelled) setResolvedSrc(src) // Fallback to original src
    })
    return () => { cancelled = true }
  }, [src, resolveImageSrc])

  if (!resolvedSrc) {
    return <div className="w-full h-20 rounded-lg bg-bg-tertiary/30 animate-pulse my-2" />
  }

  return (
    <img
      src={resolvedSrc}
      alt={alt}
      className="max-w-full rounded-lg my-2"
      onError={(e) => {
        // Fallback: hide the image if it fails to load
        ;(e.target as HTMLImageElement).style.display = 'none'
      }}
    />
  )
}

/**
 * MarkdownBlock — Cline-inspired markdown renderer.
 * Renders markdown with syntax highlighting, GFM support, and cursor animation.
 */
const MarkdownBlock = memo(({ markdown, showCursor, resolveImageSrc }: MarkdownBlockProps) => {
  if (!markdown) return null

  // NOTE: 之前这里用 `<span className="inline">` 包住 ReactMarkdown，但 react-markdown
  // 会把 fenced code block 渲染成 <div>（我们的 CodeBlock）。把 block-level <div> 放进
  // inline <span> 是无效 HTML，浏览器会自动"修复"，在实际 DOM 里提前关闭 span 再插 div，
  // 结果经常看到**双边距 / 边框错位 / 代码块"重影"**。改成 block-level <div> 就彻底没事。
  return (
    <div className="w-full min-w-0 overflow-hidden break-words">
      <div className={`[&>p:first-child]:mt-0 ${showCursor ? 'inline-cursor-container' : ''}`}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            img({ src, alt, ...props }) {
              // If resolveImageSrc is provided and src is not an absolute URL,
              // resolve it asynchronously via Electron IPC.
              if (resolveImageSrc && src && !/^https?:\/\//.test(src)) {
                return <ResolvedImage src={src} alt={alt} resolveImageSrc={resolveImageSrc} />
              }
              return <img src={src} alt={alt} className="max-w-full rounded-lg my-2" {...props} />
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
                return (
                  <CodeBlock language={match ? match[1] : 'text'} code={codeString} />
                )
              }

              // Inline code
              return (
                <code
                  className="font-mono text-[12px] bg-bg-tertiary/80 border border-border/60 rounded-[4px] px-1.5 py-0.5 whitespace-pre-line break-words text-accent-primary/90"
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
                <div className="overflow-x-auto my-3 rounded-lg border border-border">
                  <table className="border-collapse w-full text-[13px]">
                    {children}
                  </table>
                </div>
              )
            },
            th({ children }) {
              return (
                <th className="p-2.5 border-b border-border text-left bg-bg-tertiary/50 font-semibold text-text-primary text-[12px] uppercase tracking-wider">
                  {children}
                </th>
              )
            },
            td({ children }) {
              return (
                <td className="p-2.5 border-b border-border/50 text-left text-text-secondary">
                  {children}
                </td>
              )
            },
            hr() {
              return <hr className="my-4 border-border/50" />
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
    </div>
  )
})

MarkdownBlock.displayName = 'MarkdownBlock'

export default MarkdownBlock

// --- Code Block with copy button ---

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false)
  const { style: codeTheme } = useChatCodeTheme()

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [code])

  return (
    <div className="relative group my-3 rounded-xl overflow-hidden border border-border/60">
      {/* Language badge + copy button */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-bg-tertiary/80 border-b border-border/40">
        <span className="text-[10px] font-mono text-text-muted uppercase tracking-wider">{language}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-md text-text-muted hover:text-text-primary hover:bg-bg-hover transition-all duration-150"
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
          fontSize: '12px',
          lineHeight: '1.6',
          padding: '12px 16px',
          background: 'var(--bg-tertiary)',
          border: 'none',
          textShadow: 'none',
        }}
      >
        {code}
      </SyntaxHighlighter>
    </div>
  )
}
