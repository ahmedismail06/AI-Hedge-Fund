import { useAuth } from '../context/AuthContext';

const CATEGORY_STYLE = {
  NEW_ENTRY:    { bg: 'var(--green-bg)',     color: 'var(--green)',  border: 'var(--green-border)' },
  EXIT_TRIM:    { bg: 'var(--amber-bg)',     color: 'var(--amber)',  border: 'var(--amber-border)' },
  REBALANCE:    { bg: 'var(--blue-bg)',      color: 'var(--blue)',   border: 'var(--blue-border)' },
  CRISIS:       { bg: 'var(--red-bg)',       color: 'var(--red)',    border: 'var(--red-border)' },
  PRE_EARNINGS: { bg: 'var(--accent-muted)', color: 'var(--accent)', border: 'var(--accent-ring)' },
};

const EXEC_STYLE = {
  SENT_TO_EXECUTION: { bg: 'var(--green-bg)',    color: 'var(--green)' },
  BLOCKED:           { bg: 'var(--red-bg)',       color: 'var(--red)' },
  DEFERRED:          { bg: 'var(--amber-bg)',     color: 'var(--amber)' },
  NO_ACTION:         { bg: 'var(--surface-2)',    color: 'var(--text-3)' },
  PENDING_HUMAN:     { bg: 'var(--blue-bg)',      color: 'var(--blue)' },
  TRIGGERED_PIPELINE:{ bg: 'var(--accent-muted)', color: 'var(--accent)' },
};

function fmtAgo(ts) {
  if (!ts) return '—';
  const diff = (Date.now() - new Date(ts)) / 1000;
  if (diff < 60)    return `${Math.floor(diff)}s`;
  if (diff < 3600)  return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return `${Math.floor(diff / 86400)}d`;
}

export default function DecisionCard({ decision: d, onOverride, onDefer, onOpenDetail }) {
  const { isAdmin } = useAuth();

  const catSt  = CATEGORY_STYLE[d.category]     ?? { bg: 'var(--surface-2)', color: 'var(--text-3)', border: 'var(--border)' };
  const execSt = EXEC_STYLE[d.execution_status] ?? { bg: 'var(--surface-2)', color: 'var(--text-3)' };
  const conf   = d.confidence != null ? Math.round(d.confidence * 100) : null;
  const confColor = conf >= 70 ? 'var(--green)' : conf >= 50 ? 'var(--amber)' : 'var(--red)';

  return (
    <div
      className="rounded-xl px-4 py-3 transition-colors cursor-pointer hover:opacity-90"
      style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}
      onClick={() => onOpenDetail?.(d)}
    >
      {/* Top row: category + ticker + decision + time */}
      <div className="flex items-center gap-2 flex-wrap">
        <span style={{
          fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
          padding: '2px 7px', borderRadius: '4px', flexShrink: 0,
          background: catSt.bg, color: catSt.color, border: `1px solid ${catSt.border}`,
        }}>
          {d.category}
        </span>
        {d.ticker && (
          <span style={{ fontFamily: 'JetBrains Mono', fontSize: '14px', fontWeight: 800, color: 'var(--text)', flexShrink: 0 }}>
            {d.ticker}
          </span>
        )}
        <span style={{
          fontFamily: 'Syne', fontSize: '11px', fontWeight: 700, flexShrink: 0,
          padding: '2px 7px', borderRadius: '4px',
          background: execSt.bg, color: execSt.color,
        }}>
          {d.decision}
        </span>
        <span className="ml-auto flex-shrink-0" style={{ fontSize: '10px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
          {fmtAgo(d.timestamp)}
        </span>
      </div>

      {/* Confidence bar */}
      {conf != null && (
        <div className="flex items-center gap-2 mt-2">
          <div className="rounded-full overflow-hidden flex-1" style={{ height: '3px', background: 'var(--border)' }}>
            <div style={{ width: `${conf}%`, height: '100%', background: confColor }} />
          </div>
          <span style={{ fontSize: '10px', color: confColor, fontFamily: 'JetBrains Mono', flexShrink: 0 }}>{conf}%</span>
        </div>
      )}

      {/* Reasoning preview — always 2 lines, click card to see full */}
      {d.reasoning && (
        <p style={{
          fontSize: '12px', color: 'var(--text-2)', lineHeight: 1.65, margin: '8px 0 0',
          overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
        }}>
          {d.reasoning}
        </p>
      )}

      {/* Bottom row: exec status + action buttons for PENDING_HUMAN + details link */}
      <div className="flex items-center gap-2 mt-2 flex-wrap">
        <span style={{
          fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
          padding: '2px 7px', borderRadius: '4px',
          background: execSt.bg, color: execSt.color,
        }}>
          {d.execution_status?.replace(/_/g, ' ')}
        </span>

        {isAdmin && d.execution_status === 'PENDING_HUMAN' && (
          <div className="flex gap-1" onClick={e => e.stopPropagation()}>
            <button
              onClick={(e) => { e.stopPropagation(); onOverride?.(d.decision_id, 'FORCE_EXECUTE'); }}
              style={{
                padding: '3px 8px', fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
                borderRadius: '4px', cursor: 'pointer',
                background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-border)',
              }}
            >
              Approve
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onDefer?.(d.decision_id, d.ticker); }}
              style={{
                padding: '3px 8px', fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
                borderRadius: '4px', cursor: 'pointer',
                background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-border)',
              }}
            >
              Defer
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); onOverride?.(d.decision_id, 'BLOCK'); }}
              style={{
                padding: '3px 8px', fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
                borderRadius: '4px', cursor: 'pointer',
                background: 'var(--red-bg)', color: 'var(--red)', border: '1px solid var(--red-border)',
              }}
            >
              Block
            </button>
          </div>
        )}

        <button
          onClick={(e) => { e.stopPropagation(); onOpenDetail?.(d); }}
          style={{
            marginLeft: 'auto', fontSize: '9px', color: 'var(--accent)', fontFamily: 'Syne', fontWeight: 700,
            background: 'none', border: 'none', cursor: 'pointer', padding: 0,
          }}
        >
          Details →
        </button>
      </div>
    </div>
  );
}
