const SEVERITY = {
  CRITICAL: { border: 'border-red-500', bg: 'bg-red-50', badge: 'bg-red-100 text-red-700', icon: '🔴', pulse: true },
  BREACH:   { border: 'border-orange-400', bg: 'bg-orange-50', badge: 'bg-orange-100 text-orange-700', icon: '🟠', pulse: false },
  WARN:     { border: 'border-yellow-400', bg: 'bg-yellow-50', badge: 'bg-yellow-100 text-yellow-700', icon: '🟡', pulse: false },
};

function fmtTime(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    const now = new Date();
    const today = now.toDateString() === d.toDateString();
    const time = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    return today ? `Today at ${time}` : `${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} at ${time}`;
  } catch { return ts; }
}

export default function RiskAlert({ alert, onResolve, compact = false }) {
  const s = SEVERITY[alert.severity] || SEVERITY.WARN;

  return (
    <div className={`flex items-start gap-3 rounded-lg border-l-4 p-3 ${s.border} ${s.bg} ${alert.resolved ? 'opacity-50' : ''}`}>
      <span className="text-base mt-0.5 flex-shrink-0">{s.icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${s.badge}`}>
            {alert.severity}
          </span>
          <span className="text-xs text-gray-400">{fmtTime(alert.triggered_at)}</span>
          {alert.resolved && (
            <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">Resolved</span>
          )}
        </div>
        {!compact && (
          <p className="text-sm text-gray-700 mt-1 leading-snug">{alert.message}</p>
        )}
        {compact && (
          <p className="text-xs text-gray-600 mt-0.5 truncate">{alert.message}</p>
        )}
      </div>
      {onResolve && !alert.resolved && (
        <button
          onClick={() => onResolve(alert.id)}
          className="flex-shrink-0 text-xs px-2 py-1 rounded border border-gray-300 text-gray-600 hover:bg-white transition-colors"
        >
          Resolve
        </button>
      )}
    </div>
  );
}
