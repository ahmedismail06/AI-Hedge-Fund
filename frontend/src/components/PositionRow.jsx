const fmt$ = (v) =>
  v == null ? '—' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2 }).format(v);

const fmtPct = (v) => (v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`);

export default function PositionRow({ position: p, onClick }) {
  const pnlPos = (p.pnl ?? 0) >= 0;
  const stopDist = p.entry_price && p.stop_loss_price
    ? ((p.stop_loss_price - p.entry_price) / p.entry_price) * 100
    : null;
  const stopClose = stopDist != null && Math.abs(stopDist) < 3;

  return (
    <tr
      onClick={onClick}
      className="border-b border-gray-100 hover:bg-blue-50 cursor-pointer transition-colors"
    >
      <td className="px-4 py-3" data-label="Ticker">
        <div className="flex items-center gap-2">
          <span className="font-mono font-bold text-gray-900">{p.ticker}</span>
          <span className={`text-xs px-1.5 py-0.5 rounded font-medium ${
            p.direction === 'LONG' ? 'bg-blue-100 text-blue-700' : 'bg-red-100 text-red-700'
          }`}>
            {p.direction === 'LONG' ? 'BUY' : 'SELL'}
          </span>
        </div>
      </td>
      <td className="px-4 py-3 text-sm text-gray-700" data-label="Shares">{p.share_count?.toLocaleString() ?? '—'}</td>
      <td className="px-4 py-3 text-sm text-gray-700" data-label="Entry">{fmt$(p.entry_price)}</td>
      <td className="px-4 py-3 text-sm text-gray-700" data-label="Current">{fmt$(p.current_price)}</td>
      <td className={`px-4 py-3 text-sm font-medium ${pnlPos ? 'text-green-600' : 'text-red-600'}`} data-label="P&L">
        <div>{fmt$(p.pnl)}</div>
        <div className="text-xs">{fmtPct(p.pnl_pct ? p.pnl_pct * 100 : null)}</div>
      </td>
      <td className="px-4 py-3 text-sm" data-label="Stop Loss">
        <div className="text-gray-700">{fmt$(p.stop_loss_price)}</div>
        {stopDist != null && (
          <div className={`text-xs ${stopClose ? 'text-orange-500 font-medium' : 'text-gray-400'}`}>
            {stopDist.toFixed(1)}% away
          </div>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-gray-500" data-label="Size">{p.size_label ?? '—'}</td>
    </tr>
  );
}
