/**
 * CsvTableView — lightweight spreadsheet-style viewer for .csv / .tsv files.
 *
 * Design choices:
 * - No tanstack/ag-grid dependency. CSS grid with sticky headers covers 99%
 *   of the "open a CSV and eyeball it" workflow.
 * - Auto-detects the delimiter (`,` vs `\t` vs `;`) from the header row so
 *   the same viewer handles TSV and European CSV exports.
 * - Handles RFC 4180 quoted fields (supports embedded commas and "" escapes).
 * - Row count / column count / file path shown in a header strip.
 * - Search box filters rows (case-insensitive, any column).
 * - Each cell has a `title` attribute so long values are readable on hover.
 * - Long files are rendered in full but virtualised via CSS `content-visibility`
 *   so we don't need to wire up react-window for reasonable sizes (<50k rows).
 *
 * If the content is empty or all whitespace, we show a hint instead of a
 * broken grid.
 */
import { useMemo, useState } from 'react'
import { FileText, Search, X } from 'lucide-react'
import type { ViewTab } from '@/stores/viewStore'

interface CsvTableViewProps {
  tab: ViewTab
}

export function CsvTableView({ tab }: CsvTableViewProps) {
  const content = tab.content ?? ''
  const [query, setQuery] = useState('')

  const { headers, rows, delimiter } = useMemo(() => parseCsv(content), [content])

  const filteredRows = useMemo(() => {
    if (!query.trim()) return rows
    const q = query.toLowerCase()
    return rows.filter((row) => row.some((cell) => cell.toLowerCase().includes(q)))
  }, [rows, query])

  if (!content.trim()) {
    return <EmptyState message="File is empty" path={tab.filePath} />
  }

  if (headers.length === 0) {
    return <EmptyState message="Could not detect any columns" path={tab.filePath} />
  }

  return (
    <div className="flex flex-col h-full bg-bg-primary overflow-hidden">
      {/* Header strip */}
      <div className="h-8 border-b border-border flex items-center px-3 shrink-0 gap-3 text-2xs text-text-muted bg-bg-secondary">
        <FileText className="w-3.5 h-3.5 text-accent-primary shrink-0" />
        <span className="truncate flex-1" title={tab.filePath}>
          {tab.title}
        </span>
        <span>
          {rows.length.toLocaleString()} row{rows.length === 1 ? '' : 's'}
          {filteredRows.length !== rows.length && (
            <span className="text-accent-primary">
              {' '}
              ({filteredRows.length.toLocaleString()} shown)
            </span>
          )}
        </span>
        <span>·</span>
        <span>{headers.length} col{headers.length === 1 ? '' : 's'}</span>
        <span>·</span>
        <span className="font-mono">
          delim: {delimiter === '\t' ? 'TAB' : JSON.stringify(delimiter)}
        </span>
      </div>

      {/* Search bar */}
      <div className="h-8 border-b border-border flex items-center px-2 shrink-0 gap-2 bg-bg-primary">
        <div className="flex items-center gap-1.5 bg-bg-tertiary rounded-md px-2 py-1 flex-1 max-w-sm">
          <Search className="w-3 h-3 text-text-muted shrink-0" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter rows..."
            className="flex-1 bg-transparent text-xs text-text-primary placeholder:text-text-muted/50 outline-none min-w-0"
          />
          {query && (
            <button
              onClick={() => setQuery('')}
              className="text-text-muted hover:text-text-secondary shrink-0"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto scrollbar-thin">
        <table className="csv-grid text-xs border-collapse">
          <thead>
            <tr>
              <th className="csv-row-num">#</th>
              {headers.map((h, i) => (
                <th key={i} title={h}>
                  {h || <span className="text-text-muted/50 italic">col{i + 1}</span>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((row, rowIdx) => (
              <tr key={rowIdx}>
                <td className="csv-row-num">{rowIdx + 1}</td>
                {headers.map((_, colIdx) => {
                  const value = row[colIdx] ?? ''
                  return (
                    <td key={colIdx} title={value}>
                      {value || <span className="text-text-muted/30">·</span>}
                    </td>
                  )
                })}
              </tr>
            ))}
            {filteredRows.length === 0 && (
              <tr>
                <td colSpan={headers.length + 1} className="text-center py-8 text-text-muted italic">
                  No rows match "{query}"
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Scoped styling. Uses the project's design-token CSS variables
          (see src/theme/globals.css) so it adapts to both dark and light
          themes automatically. NOTE: variable names are `--bg-*` /
          `--text-*` / `--border-color`, *without* a `--color-` prefix —
          earlier drafts used the wrong names and the fallbacks silently
          forced dark colours in light mode (white-on-light-grey). */}
      <style>{`
        .csv-grid {
          width: max-content;
          min-width: 100%;
          background: var(--bg-primary);
          color: var(--text-primary);
        }
        .csv-grid thead th {
          position: sticky;
          top: 0;
          background: var(--bg-secondary);
          border-bottom: 1px solid var(--border-color);
          padding: 6px 10px;
          text-align: left;
          font-weight: 600;
          color: var(--text-primary);
          white-space: nowrap;
          z-index: 2;
        }
        .csv-grid tbody td {
          padding: 4px 10px;
          border-bottom: 1px solid var(--border-color);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          max-width: 320px;
          color: var(--text-primary);
        }
        .csv-grid tbody tr:hover td {
          background: var(--bg-hover);
        }
        .csv-grid .csv-row-num {
          position: sticky;
          left: 0;
          background: var(--bg-secondary);
          color: var(--text-muted);
          text-align: right;
          min-width: 48px;
          max-width: 48px;
          font-variant-numeric: tabular-nums;
          z-index: 1;
        }
        .csv-grid thead .csv-row-num {
          z-index: 3;
        }
        .csv-grid tbody tr {
          content-visibility: auto;
          contain-intrinsic-size: 28px;
        }
      `}</style>
    </div>
  )
}

// ─── Empty state ──────────────────────────────────────────────────

function EmptyState({ message, path }: { message: string; path?: string }) {
  return (
    <div className="h-full flex items-center justify-center bg-bg-primary">
      <div className="text-center">
        <FileText className="w-8 h-8 text-text-muted/40 mx-auto mb-2" />
        <p className="text-xs text-text-muted">{message}</p>
        {path && <p className="text-2xs text-text-muted/50 mt-1 font-mono">{path}</p>}
      </div>
    </div>
  )
}

// ─── CSV parser (RFC 4180-ish) ────────────────────────────────────

interface ParsedCsv {
  headers: string[]
  rows: string[][]
  delimiter: string
}

/**
 * Tiny CSV / TSV parser. Handles:
 * - Quoted fields with embedded delimiters and newlines
 * - "" escapes inside quoted fields
 * - Auto-detection of `,`, `\t`, or `;` based on the first non-empty line
 *
 * Intentionally NOT a full RFC parser — e.g. does not support BOM stripping
 * variations or cell type inference. For display purposes this is enough.
 */
function parseCsv(content: string): ParsedCsv {
  // Strip UTF-8 BOM if present
  const text = content.charCodeAt(0) === 0xfeff ? content.slice(1) : content

  // Find first non-empty line to detect delimiter
  const firstLineEnd = findFirstLineEnd(text)
  const firstLine = firstLineEnd === -1 ? text : text.slice(0, firstLineEnd)
  const delimiter = detectDelimiter(firstLine)

  const allRows = parseRows(text, delimiter)
  if (allRows.length === 0) {
    return { headers: [], rows: [], delimiter }
  }

  const [headerRow, ...dataRows] = allRows
  return {
    headers: headerRow,
    rows: dataRows,
    delimiter,
  }
}

function findFirstLineEnd(text: string): number {
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (ch === '\n' || ch === '\r') return i
  }
  return -1
}

function detectDelimiter(line: string): string {
  const candidates = [',', '\t', ';']
  let best = ','
  let bestCount = -1
  for (const c of candidates) {
    // Ignore commas inside quoted fields during heuristic count
    const count = countUnquoted(line, c)
    if (count > bestCount) {
      bestCount = count
      best = c
    }
  }
  return best
}

function countUnquoted(line: string, ch: string): number {
  let count = 0
  let inQuote = false
  for (let i = 0; i < line.length; i++) {
    const c = line[i]
    if (c === '"') {
      inQuote = !inQuote
    } else if (!inQuote && c === ch) {
      count++
    }
  }
  return count
}

function parseRows(text: string, delimiter: string): string[][] {
  const rows: string[][] = []
  let field = ''
  let row: string[] = []
  let inQuote = false
  let i = 0
  const len = text.length

  while (i < len) {
    const ch = text[i]

    if (inQuote) {
      if (ch === '"') {
        if (i + 1 < len && text[i + 1] === '"') {
          field += '"'
          i += 2
          continue
        }
        inQuote = false
        i++
        continue
      }
      field += ch
      i++
      continue
    }

    if (ch === '"') {
      inQuote = true
      i++
      continue
    }
    if (ch === delimiter) {
      row.push(field)
      field = ''
      i++
      continue
    }
    if (ch === '\r') {
      // Treat \r\n as a single terminator; \r alone also as a terminator.
      row.push(field)
      field = ''
      rows.push(row)
      row = []
      if (i + 1 < len && text[i + 1] === '\n') i += 2
      else i++
      continue
    }
    if (ch === '\n') {
      row.push(field)
      field = ''
      rows.push(row)
      row = []
      i++
      continue
    }
    field += ch
    i++
  }

  // Trailing field / row
  if (field.length > 0 || row.length > 0) {
    row.push(field)
    rows.push(row)
  }

  // Drop trailing fully-empty rows (common from final newline)
  while (rows.length > 0) {
    const last = rows[rows.length - 1]
    if (last.length === 1 && last[0] === '') rows.pop()
    else break
  }

  return rows
}
