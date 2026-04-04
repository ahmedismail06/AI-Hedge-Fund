const SEVERITY_STYLES = {
  CRITICAL: 'bg-red-50 border-l-4 border-red-500',
  BREACH:   'bg-orange-50 border-l-4 border-orange-400',
  WARN:     'bg-yellow-50 border-l-4 border-yellow-400',
}

const SEVERITY_BADGE = {
  CRITICAL: 'bg-red-100 text-red-700',
  BREACH:   'bg-orange-100 text-orange-700',
  WARN:     'bg-yellow-100 text-yellow-700',
}

export default function RiskAlert({ alert, onResolve }) {
  const rowClass = SEVERITY_STYLES[alert.severity] || ''
  const badgeClass = SEVERITY_BADGE[alert.severity] || 'bg-gray-100 text-gray-600'

  return (
    <tr className={rowClass}>
      <td className="px-4 py-3">
        <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${badgeClass}`}>
          {alert.severity}
        </span>
      </td>
      <td className="px-4 py-3 text-sm font-mono text-gray-900">
        {alert.ticker || <span className="text-gray-400">portfolio</span>}
      </td>
      <td className="px-4 py-3 text-sm text-gray-500">T{alert.tier}</td>
      <td className="px-4 py-3 text-sm text-gray-700 max-w-xs truncate">{alert.trigger}</td>
      <td className="px-4 py-3 text-sm text-gray-500">{alert.regime}</td>
      <td className="px-4 py-3 text-xs text-gray-400">
        {new Date(alert.created_at).toLocaleString()}
      </td>
      <td className="px-4 py-3">
        {!alert.resolved && (
          <button
            onClick={() => onResolve(alert.id)}
            className="text-xs px-2 py-1 rounded bg-white border border-gray-300 hover:bg-gray-50 text-gray-600 transition-colors"
          >
            Resolve
          </button>
        )}
        {alert.resolved && (
          <span className="text-xs text-gray-400">Resolved</span>
        )}
      </td>
    </tr>
  )
}
