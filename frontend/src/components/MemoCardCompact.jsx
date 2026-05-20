import ConvictionBadge from './ConvictionBadge';

const VERDICT_STYLES = {
  LONG:  { bg: 'var(--green-bg)', color: 'var(--green)', border: 'var(--green-border)' },
  SHORT: { bg: 'var(--red-bg)',   color: 'var(--red)',   border: 'var(--red-border)' },
  AVOID: { bg: 'var(--surface-2)', color: 'var(--text-3)', border: 'var(--border)' },
};

const STATUS_STYLES = {
  COMPLETE:   { color: 'var(--green)',  label: 'Done' },
  GENERATING: { color: 'var(--amber)',  label: 'Generating…' },
  PENDING:    { color: 'var(--text-3)', label: 'Pending' },
  ERROR:      { color: 'var(--red)',    label: 'Error' },
};

function fmtDate(d) {
  if (!d) return '';
  return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

export default function MemoCardCompact({ memo, onClick }) {
  const vs = VERDICT_STYLES[memo.verdict] ?? VERDICT_STYLES.AVOID;
  const ss = STATUS_STYLES[memo.status] ?? STATUS_STYLES.PENDING;

  return (
    <div
      className="rounded-xl p-4 cursor-pointer transition-all"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
      onClick={onClick}
      onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--border-2)'; e.currentTarget.style.background = 'var(--surface-2)'; }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.background = 'var(--surface)'; }}
    >
      {/* Header */}
      <div className="flex items-start gap-2 flex-wrap mb-2">
        <span style={{ fontFamily: 'JetBrains Mono', fontSize: '15px', fontWeight: 800, color: 'var(--text)' }}>
          {memo.ticker}
        </span>
        {memo.verdict && (
          <span style={{
            fontSize: '10px', fontWeight: 700, fontFamily: 'Syne',
            padding: '2px 7px', borderRadius: '4px',
            background: vs.bg, color: vs.color, border: `1px solid ${vs.border}`,
          }}>
            {memo.verdict}
          </span>
        )}
        {memo.conviction_score != null && (
          <ConvictionBadge score={memo.conviction_score} />
        )}
        <div className="ml-auto flex items-center gap-2">
          <span style={{ fontSize: '9px', color: ss.color, fontFamily: 'Syne', fontWeight: 700 }}>
            {ss.label}
          </span>
          <span style={{ fontSize: '10px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
            {fmtDate(memo.date)}
          </span>
        </div>
      </div>

      {/* One-line thesis */}
      {memo.thesis && (
        <p style={{
          fontSize: '11px',
          color: 'var(--text-2)',
          lineHeight: 1.55,
          overflow: 'hidden',
          display: '-webkit-box',
          WebkitLineClamp: 2,
          WebkitBoxOrient: 'vertical',
          marginBottom: memo.variant_perception ? '6px' : 0,
        }}>
          {memo.thesis}
        </p>
      )}

      {/* Variant perception (italic, truncated) */}
      {memo.variant_perception && (
        <p style={{
          fontSize: '10px',
          color: 'var(--text-3)',
          fontStyle: 'italic',
          overflow: 'hidden',
          display: '-webkit-box',
          WebkitLineClamp: 1,
          WebkitBoxOrient: 'vertical',
        }}>
          "{memo.variant_perception}"
        </p>
      )}

      {/* Catalyst */}
      {memo.repricing_catalyst && (
        <div className="mt-2 flex items-center gap-1">
          <span className="material-symbols-outlined" style={{ fontSize: '11px', color: 'var(--accent)' }}>bolt</span>
          <span style={{ fontSize: '10px', color: 'var(--text-3)' }}>{memo.repricing_catalyst}</span>
        </div>
      )}
    </div>
  );
}
