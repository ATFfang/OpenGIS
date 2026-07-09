import { WORKER_RESOURCE_HISTORY_LIMIT } from './workerUtils'

export function ResourceSparkline({
  values,
  maxValue,
  colorClass = 'text-accent-primary',
}: {
  values: Array<number | null>
  maxValue?: number
  colorClass?: string
}) {
  const valid = values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  const observedMax = valid.length > 0 ? Math.max(...valid) : 0
  const scaleMax = maxValue ?? Math.max(1, observedMax * 1.25)
  const width = 96
  const height = 26
  const points = values.slice(-WORKER_RESOURCE_HISTORY_LIMIT)
  const coordinates = points.map((value, index) => {
    const x = points.length <= 1 ? width : (index / (points.length - 1)) * width
    const numeric = typeof value === 'number' && Number.isFinite(value) ? value : 0
    const y = height - Math.max(0, Math.min(1, numeric / scaleMax)) * (height - 3) - 1.5
    return { x, y }
  })
  const linePath = coordinates
    .map(({ x, y }, index) => `${index === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(' ')
  const areaPath = coordinates.length > 1
    ? `${linePath} L${coordinates[coordinates.length - 1].x.toFixed(1)},${height - 1} L${coordinates[0].x.toFixed(1)},${height - 1} Z`
    : ''

  return (
    <svg className={`mt-2 h-[26px] w-full overflow-visible ${colorClass}`} viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" aria-hidden>
      <path d={`M0,${height - 1} L${width},${height - 1}`} stroke="currentColor" strokeOpacity="0.12" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      {points.length > 1 && (
        <>
          <path d={areaPath} fill="currentColor" fillOpacity="0.14" />
          <path d={linePath} fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        </>
      )}
    </svg>
  )
}
