export default function ExposureBar({ label, current = 0, limit = 1, regime }) {
  const pct = limit > 0 ? Math.min((current / limit) * 100, 100) : 0
  const isWarning = pct > 80
  const isBreach = current > limit

  const barColor = isBreach
    ? 'bg-red-500'
    : isWarning
    ? 'bg-orange-400'
    : 'bg-blue-500'

  return (
    <div className="flex items-center gap-3">
      <span className="text-xs text-gray-500 w-10 flex-shrink-0">{label}</span>
      <div className="flex-1 h-2 bg-gray-200 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span
        className={`text-xs font-mono w-28 text-right ${
          isBreach ? 'text-red-600 font-semibold' : 'text-gray-600'
        }`}
      >
        {(current * 100).toFixed(1)}% / {(limit * 100).toFixed(0)}%
      </span>
      {regime && (
        <span className="text-[10px] text-gray-400 uppercase tracking-wide">
          {regime}
        </span>
      )}
    </div>
  )
}
