import { Table } from 'lucide-react'

/**
 * Data table panel — displays attribute data from loaded GIS layers.
 * Uses TanStack Table for headless table functionality.
 */
export function DataTable() {
  return (
    <div className="w-full h-full bg-bg-primary border-t border-border flex flex-col">
      {/* Header */}
      <div className="h-9 border-b border-border flex items-center px-3 shrink-0">
        <Table className="w-3.5 h-3.5 text-text-muted mr-2" />
        <span className="text-xs font-medium text-text-secondary">Attribute Table</span>
        <span className="text-xs text-text-muted ml-2">No data loaded</span>
      </div>

      {/* Table content */}
      <div className="flex-1 flex items-center justify-center text-sm text-text-muted">
        <p>Select a layer to view its attribute data</p>
      </div>
    </div>
  )
}
