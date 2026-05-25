import { Table } from 'lucide-react'
import { useT } from '@/i18n'

/**
 * Data table panel — displays attribute data from loaded GIS layers.
 * Uses TanStack Table for headless table functionality.
 */
export function DataTable() {
  const t = useT()

  return (
    <div className="w-full h-full bg-bg-primary border-t border-border flex flex-col">
      {/* Header */}
      <div className="h-9 border-b border-border flex items-center px-3 shrink-0">
        <Table className="w-3.5 h-3.5 text-text-muted mr-2" />
        <span className="text-xs font-medium text-text-secondary">{t.dataTable.title}</span>
        <span className="text-xs text-text-muted ml-2">{t.dataTable.noData}</span>
      </div>

      {/* Table content */}
      <div className="flex-1 flex items-center justify-center text-sm text-text-muted">
        <p>{t.dataTable.selectLayer}</p>
      </div>
    </div>
  )
}
