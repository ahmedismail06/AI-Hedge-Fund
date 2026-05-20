import ConvictionBadge from './ConvictionBadge';

const fmt$ = (v) => {
  if (v == null) return '—';
  const abs = Math.abs(v);
  const prefix = v < 0 ? '-' : '';
  if (abs >= 1_000_000) return `${prefix}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000)     return `${prefix}$${(abs / 1_000).toFixed(1)}k`;
  return `${prefix}$${abs.toFixed(2)}`;
};

function StopBar({ entry, stop }) {
  if (!entry || !stop) return null;
  const dist = Math.abs((stop - entry) / entry);
  const pct  = Math.min(dist * 300, 100);
  const color = dist < 0.03 ? 'var(--red)' : dist < 0.06 ? 'var(--amber)' : 'var(--green)';
  return (
    <div className="flex items-center gap-1.5 mt-0.5">
      <div className="rounded-full overflow-hidden" style={{ width: '40px', height: '3px', background: 'var(--border)' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color }} />
      </div>
      <span style={{ fontSize: '9px', color, fontFamily: 'JetBrains Mono' }}>
        {(dist * 100).toFixed(1)}%
      </span>
    </div>
  );
}

export default function PositionTableCompact({ positions, onTickerClick, memos = {} }) {
  if (!positions || positions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-8">
        <span className="material-symbols-outlined" style={{ fontSize: '28px', color: 'var(--border-2)' }}>
          account_balance_wallet
        </span>
        <span style={{ fontSize: '12px', color: 'var(--text-3)' }}>No open positions</span>
      </div>
    );
  }

  return (
    <div className="space-y-px term-scroll" style={{ maxHeight: '100%', overflowY: 'auto' }}>
      {positions.map((p) => {
        const isLong   = p.direction === 'LONG';
        const entry    = p.entry_price ?? 0;
        const current  = p.current_price ?? entry;
        const shares   = p.share_count ?? 0;
        const pnl      = isLong ? (current - entry) * shares : (entry - current) * shares;
        const pnlPos   = pnl >= 0;
        const heatCls  = pnl > 50 ? 'heat-row-green' : pnl < -50 ? 'heat-row-red' : 'heat-row-flat';
        const memo     = memos[p.ticker];

        return (
          <div
            key={p.ticker ?? p.id}
            className={`flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-all ${heatCls}`}
            onClick={() => onTickerClick?.(p, memo)}
            onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-2)'; }}
            onMouseLeave={e => { e.currentTarget.style.background = ''; e.currentTarget.classList.add(heatCls); }}
            onMouseEnterCapture={e => { e.currentTarget.classList.remove(heatCls); }}
          >
            {/* Ticker + direction */}
            <div className="flex-shrink-0 w-20">
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: '13px', fontWeight: 700, color: 'var(--text)' }}>
                {p.ticker}
              </div>
              <div style={{ fontSize: '9px', fontWeight: 600, color: isLong ? 'var(--blue)' : 'var(--red)', fontFamily: 'Syne' }}>
                {isLong ? 'LONG' : 'SHORT'}
              </div>
            </div>

            {/* P&L */}
            <div className="flex-1 min-w-0">
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', fontWeight: 700, color: pnlPos ? 'var(--green)' : 'var(--red)' }}>
                {pnlPos ? '+' : ''}{fmt$(pnl)}
              </div>
              <StopBar entry={entry} stop={p.stop_loss_price} />
            </div>

            {/* Size label + conviction */}
            <div className="flex-shrink-0 text-right">
              {p.size_label && (
                <div style={{ fontSize: '9px', fontWeight: 700, color: 'var(--text-3)', fontFamily: 'Syne' }}>
                  {p.size_label}
                </div>
              )}
              {memo?.conviction_score != null && (
                <ConvictionBadge score={memo.conviction_score} />
              )}
            </div>

            <span className="material-symbols-outlined flex-shrink-0" style={{ fontSize: '14px', color: 'var(--text-3)' }}>
              chevron_right
            </span>
          </div>
        );
      })}
    </div>
  );
}
