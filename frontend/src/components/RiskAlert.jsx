const SEVERITY_STYLES = {
  CRITICAL: {
    borderVar:   'var(--red)',
    dotClass:    'dot-red pulse-critical',
    badgeBgVar:  'var(--red-bg)',
    badgeColVar: 'var(--red)',
    textVar:     'var(--text)',
  },
  BREACH: {
    borderVar:   'var(--orange)',
    dotClass:    'dot-amber',
    badgeBgVar:  'var(--orange-bg)',
    badgeColVar: 'var(--orange)',
    textVar:     'var(--text)',
  },
  WARN: {
    borderVar:   'var(--amber)',
    dotClass:    'dot-amber',
    badgeBgVar:  'var(--amber-bg)',
    badgeColVar: 'var(--amber)',
    textVar:     'var(--text)',
  },
};

function fmtTime(ts) {
  if (!ts) return '';
  try {
    const d     = new Date(ts);
    const now   = new Date();
    const today = now.toDateString() === d.toDateString();
    const time  = d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    return today
      ? `Today ${time}`
      : `${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })} ${time}`;
  } catch { return ts; }
}

export default function RiskAlert({ alert, onResolve, compact = false }) {
  const s = SEVERITY_STYLES[alert.severity] || SEVERITY_STYLES.WARN;

  return (
    <div
      className={`flex items-start gap-3 rounded-md p-3 transition-opacity ${alert.resolved ? 'opacity-40' : ''}`}
      style={{
        background:  'var(--surface)',
        border:      `1px solid ${alert.resolved ? 'var(--border)' : s.borderVar}`,
        borderLeft:  `3px solid ${s.borderVar}`,
      }}
    >
      <span className={`w-2 h-2 rounded-full mt-1 flex-shrink-0 ${s.dotClass}`} />

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className="text-[10px] font-bold px-1.5 py-0.5 rounded-sm tracking-wider"
            style={{
              background:  s.badgeBgVar,
              color:       s.badgeColVar,
              fontFamily:  'Syne',
            }}
          >
            {alert.severity}
          </span>
          <span className="text-[10px] font-data" style={{ color: 'var(--text-3)' }}>
            {fmtTime(alert.triggered_at)}
          </span>
          {alert.resolved && (
            <span
              className="text-[10px] px-1.5 py-0.5 rounded-sm"
              style={{ background: 'var(--surface-2)', color: 'var(--text-2)' }}
            >
              Resolved
            </span>
          )}
        </div>

        {!compact ? (
          <p className="text-sm mt-1.5 leading-snug" style={{ color: s.textVar }}>
            {alert.message}
          </p>
        ) : (
          <p className="text-[11px] mt-0.5 truncate font-data" style={{ color: 'var(--text-2)' }}>
            {alert.message}
          </p>
        )}
      </div>

      {onResolve && !alert.resolved && (
        <button
          onClick={() => onResolve(alert.id)}
          className="flex-shrink-0 text-[10px] px-2 py-1 rounded-sm font-bold tracking-wide transition-colors"
          style={{
            background: 'var(--surface-2)',
            border:     '1px solid var(--border-2)',
            color:      'var(--text-2)',
            fontFamily: 'Syne',
          }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--accent)'; }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-2)'; e.currentTarget.style.color = 'var(--text-2)'; }}
        >
          Resolve
        </button>
      )}
    </div>
  );
}
