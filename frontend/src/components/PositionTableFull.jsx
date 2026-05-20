const fmt$ = (v, decimals = 2) => {
  if (v == null) return '—';
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(v);
};

const fmtPct = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`;

const HEADERS = ['Ticker', 'Direction', 'Shares', 'Entry', 'Current', 'P&L', 'P&L %', 'Stop', 'Distance', 'Size'];

export default function PositionTableFull({ positions, onTickerClick }) {
  if (!positions || positions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-12">
        <span className="material-symbols-outlined" style={{ fontSize: '32px', color: 'var(--border-2)' }}>
          account_balance_wallet
        </span>
        <span style={{ fontSize: '13px', color: 'var(--text-3)' }}>No open positions</span>
      </div>
    );
  }

  return (
    <div className="responsive-table-wrap">
      <table className="responsive-table w-full text-left" style={{ borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ background: 'var(--surface-2)', color: 'var(--text-3)' }}>
            {HEADERS.map(h => (
              <th
                key={h}
                className="px-4 py-2"
                style={{ fontSize: '10px', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', fontFamily: 'Syne', whiteSpace: 'nowrap' }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {positions.map((p, i) => {
            const isLong  = p.direction === 'LONG';
            const entry   = p.entry_price ?? 0;
            const current = p.current_price ?? entry;
            const shares  = p.share_count ?? 0;
            const pnl     = isLong ? (current - entry) * shares : (entry - current) * shares;
            const pnlPct  = entry > 0 ? (isLong ? (current - entry) / entry : (entry - current) / entry) : 0;
            const pnlPos  = pnl >= 0;
            const stopDist = entry > 0 && p.stop_loss_price
              ? ((p.stop_loss_price - entry) / entry)
              : null;
            const stopClose = stopDist != null && Math.abs(stopDist) < 0.05;
            const heatBg = pnl > 50 ? 'rgba(0,217,138,0.03)' : pnl < -50 ? 'rgba(255,51,71,0.03)' : 'transparent';

            return (
              <tr
                key={p.id ?? p.ticker ?? i}
                style={{ borderBottom: '1px solid var(--border)', background: heatBg, cursor: 'pointer', transition: 'background 0.1s' }}
                onClick={() => onTickerClick?.(p)}
                onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-2)'; }}
                onMouseLeave={e => { e.currentTarget.style.background = heatBg; }}
              >
                <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontWeight: 700, fontSize: '13px', color: 'var(--text)', whiteSpace: 'nowrap' }}>
                  {p.ticker}
                </td>
                <td className="px-4 py-3">
                  <span style={{
                    fontSize: '10px', fontWeight: 700, fontFamily: 'Syne',
                    padding: '2px 7px', borderRadius: '4px',
                    background: isLong ? 'var(--blue-bg)' : 'var(--red-bg)',
                    color: isLong ? 'var(--blue)' : 'var(--red)',
                    border: `1px solid ${isLong ? 'var(--blue-border)' : 'var(--red-border)'}`,
                  }}>
                    {p.direction}
                  </span>
                </td>
                <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', color: 'var(--text-2)' }}>
                  {shares.toLocaleString()}
                </td>
                <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', color: 'var(--text-2)' }}>
                  {fmt$(entry)}
                </td>
                <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', color: 'var(--text-2)' }}>
                  {fmt$(current)}
                </td>
                <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', fontWeight: 600, color: pnlPos ? 'var(--green)' : 'var(--red)' }}>
                  {pnlPos ? '+' : ''}{fmt$(pnl)}
                </td>
                <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', color: pnlPos ? 'var(--green)' : 'var(--red)' }}>
                  {fmtPct(pnlPct)}
                </td>
                <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', color: stopClose ? 'var(--red)' : 'var(--text-2)' }}>
                  {fmt$(p.stop_loss_price)}
                </td>
                <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', color: stopClose ? 'var(--red)' : 'var(--text-3)' }}>
                  {stopDist != null ? `${(stopDist * 100).toFixed(1)}%` : '—'}
                </td>
                <td className="px-4 py-3" style={{ fontFamily: 'Syne', fontSize: '11px', fontWeight: 600, color: 'var(--text-2)' }}>
                  {p.size_label ?? '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
