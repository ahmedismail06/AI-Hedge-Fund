export default function PositionRow({ position }) {
  const {
    ticker, direction, share_count, entry_price,
    current_price, pnl, pnl_pct, stop_loss_price,
    target_price, risk_reward_ratio,
  } = position

  const pnlPositive = (pnl ?? 0) >= 0
  const pnlClass = pnlPositive ? 'text-green-600' : 'text-red-600'

  const fmt = (v, decimals = 2) =>
    v != null ? Number(v).toFixed(decimals) : '—'

  const fmtDollar = (v) =>
    v != null
      ? `${Number(v) >= 0 ? '+' : ''}$${Math.abs(Number(v)).toLocaleString('en-US', { minimumFractionDigits: 2 })}`
      : '—'

  return (
    <tr className="hover:bg-gray-50 transition-colors">
      <td className="px-4 py-3 font-mono font-semibold text-sm text-gray-900">{ticker}</td>
      <td className="px-4 py-3">
        <span
          className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${
            direction === 'LONG' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
          }`}
        >
          {direction}
        </span>
      </td>
      <td className="px-4 py-3 text-sm text-gray-700 text-right">{fmt(share_count, 0)}</td>
      <td className="px-4 py-3 text-sm text-gray-700 text-right font-mono">${fmt(entry_price, 2)}</td>
      <td className="px-4 py-3 text-sm text-gray-700 text-right font-mono">
        {current_price != null ? `$${fmt(current_price, 2)}` : <span className="text-gray-400">—</span>}
      </td>
      <td className={`px-4 py-3 text-sm text-right font-mono font-medium ${pnlClass}`}>
        {fmtDollar(pnl)}
      </td>
      <td className={`px-4 py-3 text-sm text-right font-mono ${pnlClass}`}>
        {pnl_pct != null ? `${(Number(pnl_pct) * 100).toFixed(2)}%` : '—'}
      </td>
      <td className="px-4 py-3 text-sm text-right font-mono text-gray-500">
        {stop_loss_price != null ? `$${fmt(stop_loss_price, 2)}` : '—'}
      </td>
      <td className="px-4 py-3 text-sm text-right font-mono text-gray-500">
        {target_price != null ? `$${fmt(target_price, 2)}` : '—'}
      </td>
      <td className="px-4 py-3 text-sm text-right text-gray-500">
        {risk_reward_ratio != null ? `${fmt(risk_reward_ratio, 1)}x` : '—'}
      </td>
    </tr>
  )
}
