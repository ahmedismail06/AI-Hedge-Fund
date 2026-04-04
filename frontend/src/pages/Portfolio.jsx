import { useEffect, useState } from 'react'
import {
  getExposure,
  getHistory,
  getPending,
  getPositions,
  approvePosition,
  rejectPosition,
} from '../api/portfolio'
import ConfirmDialog from '../components/ConfirmDialog'

const TABS = [
  { id: 'pending', label: 'Pending Approvals' },
  { id: 'open', label: 'Open Positions' },
  { id: 'closed', label: 'Closed' },
]

const fmtPct = (v, digits = 1) => `${Number(v).toFixed(digits)}%`
const fmtMoney = (v, digits = 2, sign = false) => {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  const prefix = sign ? (n >= 0 ? '+' : '-') : ''
  return `${prefix}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

export default function Portfolio() {
  const [tab, setTab] = useState('pending')
  const [positions, setPositions] = useState([])
  const [pending, setPending] = useState([])
  const [history, setHistory] = useState([])
  const [exposure, setExposure] = useState(null)
  const [confirm, setConfirm] = useState(null)

  const load = () => {
    getPositions().then(r => setPositions(r.data || [])).catch(() => {})
    getPending().then(r => setPending(r.data || [])).catch(() => {})
    getHistory().then(r => setHistory(r.data || [])).catch(() => {})
    getExposure().then(r => setExposure(r.data)).catch(() => {})
  }

  useEffect(() => {
    load()
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [])

  const openPositions = positions.filter(p => p.status === 'OPEN')
  const closedPositions = history.length
    ? history
    : positions.filter(p => p.status === 'CLOSED' || p.status === 'REJECTED')

  const grossLimit = exposure?.caps?.max_gross_pct ?? 1.5
  const netLimit = exposure?.caps?.max_net_long_pct ?? 0.5
  const grossCurrent = exposure?.gross_exposure_pct ?? 0
  const netCurrent = Math.abs(exposure?.net_exposure_pct ?? 0)
  const regimeLabel = exposure?.regime || 'RISK-ON'

  const grossUtil = grossLimit > 0 ? (grossCurrent / grossLimit) * 100 : 0
  const netUtil = netLimit > 0 ? (netCurrent / netLimit) * 100 : 0

  const dayPnl = openPositions.reduce((s, p) => s + (Number(p.pnl) || 0), 0)
  const avgPnlPct = openPositions.length
    ? openPositions.reduce((s, p) => s + (Number(p.pnl_pct) || 0), 0) / openPositions.length
    : 0

  const handleConfirm = async () => {
    if (!confirm) return
    try {
      if (confirm.type === 'approve') await approvePosition(confirm.id)
      else await rejectPosition(confirm.id)
      load()
    } catch {
      // silent
    } finally {
      setConfirm(null)
    }
  }

  return (
    <div>
      <header className="flex justify-between items-center px-8 py-6 w-full bg-transparent">
        <div className="flex items-center gap-6">
          <h1 className="text-2xl font-bold tracking-tight text-on-surface font-headline">Portfolio Management</h1>
          <div className="relative group">
            <span className="absolute inset-y-0 left-3 flex items-center text-on-surface-variant">
              <span className="material-symbols-outlined text-lg">search</span>
            </span>
            <input
              className="pl-10 pr-4 py-2 bg-surface-container-low border-none rounded-xl text-sm w-64 focus:ring-2 focus:ring-primary/20 focus:bg-surface-container-lowest transition-all"
              placeholder="Search positions..."
              type="text"
            />
          </div>
        </div>
        <div className="flex items-center gap-4">
          <button className="p-2 text-on-surface-variant hover:bg-slate-100 rounded-lg transition-all scale-up active:scale-95">
            <span className="material-symbols-outlined">notifications</span>
          </button>
          <button className="p-2 text-on-surface-variant hover:bg-slate-100 rounded-lg transition-all scale-up active:scale-95">
            <span className="material-symbols-outlined">history</span>
          </button>
          <div className="w-10 h-10 rounded-full overflow-hidden bg-surface-container-high ml-2 border border-outline-variant/20">
            <img
              alt="User profile"
              src="https://lh3.googleusercontent.com/aida-public/AB6AXuAclNaPeG4Ch5YIBfg1qMJnfKxGUpVF27ejOVz7tvAG2kfiq4zqKHwuGQDSOThI-aXhuv78pMzjOhiEJvUnvc2VDL1UBh4OHlZcPR9tkVP8kPMbrZp0ihO2VSIlCbQL-LdDhw82isPYudcPabJRgywDdHWzY-5EhpW5FOVoQVU-1Iej8CSr3Y7lSOojYSVhFmp9M0kdrZrBQMr5ECUd3VLKoLoLZJNdmBIX2H-SFKe5ypn4BJzexfaVjbLzbBOfZb95hnpNQecsSnA"
            />
          </div>
        </div>
      </header>

      <div className="px-8 pb-12 space-y-8">
        <section className="bg-surface-container-lowest rounded-xl p-6 ghost-border">
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-3">
              <div className="w-1.5 h-6 bg-primary rounded-full"></div>
              <h2 className="text-sm font-semibold text-on-surface-variant uppercase tracking-widest">Global Exposure Limits</h2>
            </div>
            <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-on-primary-fixed text-primary-fixed text-[10px] font-bold">
              <span className="material-symbols-outlined text-xs" style={{ fontVariationSettings: "'FILL' 1" }}>
                trending_up
              </span>
              CURRENT REGIME: {regimeLabel}
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-12">
            <div className="space-y-3">
              <div className="flex justify-between items-end">
                <span className="text-[11px] font-bold text-on-surface-variant uppercase">Gross Exposure</span>
                <span className="text-2xl font-bold text-on-surface">
                  {fmtPct(grossCurrent * 100)} <span className="text-sm font-normal text-on-surface-variant">/ {fmtPct(grossLimit * 100, 0)}</span>
                </span>
              </div>
              <div className="h-2 w-full bg-surface-container-low rounded-full overflow-hidden">
                <div className="h-full signature-gradient rounded-full" style={{ width: `${Math.min(grossUtil, 100)}%` }} />
              </div>
              <div className="flex justify-between text-[10px] text-on-surface-variant font-medium">
                <span>0%</span>
                <span>UTILIZATION: {fmtPct(grossUtil, 0)}</span>
                <span>LIMIT</span>
              </div>
            </div>
            <div className="space-y-3">
              <div className="flex justify-between items-end">
                <span className="text-[11px] font-bold text-on-surface-variant uppercase">Net Exposure</span>
                <span className="text-2xl font-bold text-on-surface">
                  {fmtPct(netCurrent * 100)} <span className="text-sm font-normal text-on-surface-variant">/ {fmtPct(netLimit * 100, 0)}</span>
                </span>
              </div>
              <div className="h-2 w-full bg-surface-container-low rounded-full overflow-hidden">
                <div className="h-full bg-secondary-container rounded-full" style={{ width: `${Math.min(netUtil, 100)}%` }} />
              </div>
              <div className="flex justify-between text-[10px] text-on-surface-variant font-medium">
                <span>0%</span>
                <span>UTILIZATION: {fmtPct(netUtil, 0)}</span>
                <span>LIMIT</span>
              </div>
            </div>
          </div>
        </section>

        <div className="flex border-b border-outline-variant/10 gap-8">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`pb-4 text-sm font-bold transition-all ${
                tab === t.id
                  ? 'text-primary border-b-2 border-primary'
                  : 'text-on-surface-variant hover:text-on-surface'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'pending' && (
          <section className="space-y-4">
            <div className="flex items-center justify-between">
              <p className="text-xs text-on-surface-variant">
                Showing {pending.length} autonomous trade signal{pending.length === 1 ? '' : 's'} awaiting supervisor confirmation.
              </p>
              <div className="flex gap-2">
                <button className="px-4 py-2 bg-surface-container-low text-xs font-bold text-on-surface-variant rounded-lg hover:bg-surface-container-high transition-colors">
                  EXPORT LIST
                </button>
                <button className="px-4 py-2 signature-gradient text-xs font-bold text-white rounded-lg shadow-sm hover:opacity-90 transition-opacity">
                  APPROVE ALL
                </button>
              </div>
            </div>
            <div className="bg-surface-container-lowest rounded-xl ghost-border overflow-hidden">
              <table className="w-full text-left border-collapse">
                <thead className="bg-surface-container-low">
                  <tr>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest">Ticker</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-center">Dir</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Shares</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">$ Size</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-center">Conv</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest">Regime</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest">Sizing Rationale</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-center">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-outline-variant/5">
                  {pending.length === 0 && (
                    <tr>
                      <td colSpan="8" className="py-6 text-center text-sm text-on-surface-variant">
                        No pending approvals
                      </td>
                    </tr>
                  )}
                  {pending.map(p => (
                    <tr key={p.id} className="hover:bg-surface-container-high transition-colors group">
                      <td className="py-5 px-6 font-bold text-on-surface">{p.ticker}</td>
                      <td className="py-5 px-6 text-center">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                          p.direction === 'LONG' ? 'bg-primary/10 text-primary' : 'bg-tertiary/10 text-tertiary'
                        }`}>
                          {p.direction}
                        </span>
                      </td>
                      <td className="py-5 px-6 text-right font-medium">{Number(p.share_count).toFixed(0)}sh</td>
                      <td className="py-5 px-6 text-right font-semibold">${Number(p.dollar_size).toLocaleString('en-US', { maximumFractionDigits: 0 })}</td>
                      <td className="py-5 px-6 text-center">
                        <div className="flex items-center justify-center gap-1.5">
                          <div className="w-8 h-1 bg-primary-fixed-dim rounded-full overflow-hidden">
                            <div className="h-full bg-primary" style={{ width: `${Math.min(Number(p.conviction_score || 0) * 10, 100)}%` }} />
                          </div>
                          <span className="text-xs font-bold">{Number(p.conviction_score || 0).toFixed(1)}</span>
                        </div>
                      </td>
                      <td className="py-5 px-6">
                        <span className="text-[10px] font-bold text-tertiary uppercase flex items-center gap-1">
                          <span className="w-1.5 h-1.5 rounded-full bg-tertiary"></span>
                          {p.regime_at_sizing || regimeLabel}
                        </span>
                      </td>
                      <td className="py-5 px-6 text-xs text-on-surface-variant italic">{p.sizing_rationale || '—'}</td>
                      <td className="py-5 px-6 text-center">
                        <div className="flex items-center justify-center gap-2">
                          <button
                            onClick={() => setConfirm({ type: 'approve', id: p.id, ticker: p.ticker })}
                            className="p-1.5 bg-green-50 text-green-700 rounded-lg hover:bg-green-100 transition-colors"
                          >
                            <span className="material-symbols-outlined text-lg">check</span>
                          </button>
                          <button
                            onClick={() => setConfirm({ type: 'reject', id: p.id, ticker: p.ticker })}
                            className="p-1.5 bg-red-50 text-red-700 rounded-lg hover:bg-red-100 transition-colors"
                          >
                            <span className="material-symbols-outlined text-lg">close</span>
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {tab === 'open' && (
          <section className="space-y-6">
            <div className="flex items-center gap-4 mt-6">
              <h3 className="text-lg font-bold text-on-surface font-headline">Open Positions Summary</h3>
              <div className="flex-1 h-[1px] bg-outline-variant/10"></div>
              <div className="flex gap-4">
                <div className="flex flex-col items-end">
                  <span className="text-[10px] font-bold text-on-surface-variant uppercase tracking-tighter">Day&apos;s P&amp;L</span>
                  <span className={`text-sm font-bold ${dayPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                    {fmtMoney(dayPnl, 2, true)} ({fmtPct(avgPnlPct * 100, 1)})
                  </span>
                </div>
              </div>
            </div>

            <div className="bg-surface-container-lowest rounded-xl ghost-border overflow-hidden">
              <table className="w-full text-left border-collapse">
                <thead className="bg-surface-container-low">
                  <tr>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest">Ticker</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-center">Dir</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Shares</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Entry</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Current</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">P&amp;L $</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">P&amp;L %</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Stop</th>
                    <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Target</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-outline-variant/5">
                  {openPositions.length === 0 && (
                    <tr>
                      <td colSpan="9" className="py-6 text-center text-sm text-on-surface-variant">
                        No open positions
                      </td>
                    </tr>
                  )}
                  {openPositions.map(p => {
                    const pnlPositive = (p.pnl ?? 0) >= 0
                    return (
                      <tr key={p.id} className="hover:bg-surface-container-high transition-colors">
                        <td className="py-4 px-6 font-bold text-on-surface">{p.ticker}</td>
                        <td className="py-4 px-6 text-center">
                          <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                            p.direction === 'LONG' ? 'bg-primary/10 text-primary' : 'bg-tertiary/10 text-tertiary'
                          }`}>
                            {p.direction}
                          </span>
                        </td>
                        <td className="py-4 px-6 text-right">{Number(p.share_count || 0).toFixed(0)}</td>
                        <td className="py-4 px-6 text-right text-xs font-medium text-on-surface-variant">
                          ${Number(p.entry_price || 0).toFixed(2)}
                        </td>
                        <td className="py-4 px-6 text-right text-xs font-bold">${Number(p.current_price || 0).toFixed(2)}</td>
                        <td className={`py-4 px-6 text-right text-xs font-bold ${pnlPositive ? 'text-green-600' : 'text-red-600'}`}>
                          {fmtMoney(p.pnl, 2, true)}
                        </td>
                        <td className={`py-4 px-6 text-right text-xs font-bold ${pnlPositive ? 'text-green-600' : 'text-red-600'}`}>
                          {p.pnl_pct != null ? `${(Number(p.pnl_pct) * 100).toFixed(2)}%` : '—'}
                        </td>
                        <td className="py-4 px-6 text-right text-xs text-on-surface-variant">
                          {p.stop_loss_price != null ? `$${Number(p.stop_loss_price).toFixed(2)}` : '—'}
                        </td>
                        <td className="py-4 px-6 text-right text-xs text-on-surface-variant">
                          {p.target_price != null ? `$${Number(p.target_price).toFixed(2)}` : '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {tab === 'closed' && (
          <section className="bg-surface-container-lowest rounded-xl ghost-border overflow-hidden">
            <table className="w-full text-left border-collapse">
              <thead className="bg-surface-container-low">
                <tr>
                  <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest">Ticker</th>
                  <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-center">Dir</th>
                  <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Entry</th>
                  <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Exit</th>
                  <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">P&amp;L $</th>
                  <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest text-right">P&amp;L %</th>
                  <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest">Opened</th>
                  <th className="py-4 px-6 text-[10px] font-bold text-on-surface-variant uppercase tracking-widest">Closed</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-outline-variant/5">
                {closedPositions.length === 0 && (
                  <tr>
                    <td colSpan="8" className="py-6 text-center text-sm text-on-surface-variant">
                      No closed positions yet
                    </td>
                  </tr>
                )}
                {closedPositions.map(p => {
                  const pnlPositive = (p.pnl ?? 0) >= 0
                  return (
                    <tr key={p.id} className="hover:bg-surface-container-high transition-colors">
                      <td className="py-4 px-6 font-bold text-on-surface">{p.ticker}</td>
                      <td className="py-4 px-6 text-center">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase ${
                          p.direction === 'LONG' ? 'bg-primary/10 text-primary' : 'bg-tertiary/10 text-tertiary'
                        }`}>
                          {p.direction}
                        </span>
                      </td>
                      <td className="py-4 px-6 text-right text-xs font-medium text-on-surface-variant">
                        ${Number(p.entry_price || 0).toFixed(2)}
                      </td>
                      <td className="py-4 px-6 text-right text-xs font-medium text-on-surface-variant">
                        ${Number(p.current_price || p.exit_price || 0).toFixed(2)}
                      </td>
                      <td className={`py-4 px-6 text-right text-xs font-bold ${pnlPositive ? 'text-green-600' : 'text-red-600'}`}>
                        {fmtMoney(p.pnl, 2, true)}
                      </td>
                      <td className={`py-4 px-6 text-right text-xs font-bold ${pnlPositive ? 'text-green-600' : 'text-red-600'}`}>
                        {p.pnl_pct != null ? `${(Number(p.pnl_pct) * 100).toFixed(2)}%` : '—'}
                      </td>
                      <td className="py-4 px-6 text-xs text-on-surface-variant">
                        {p.opened_at ? new Date(p.opened_at).toLocaleDateString() : '—'}
                      </td>
                      <td className="py-4 px-6 text-xs text-on-surface-variant">
                        {p.closed_at ? new Date(p.closed_at).toLocaleDateString() : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </section>
        )}
      </div>

      {confirm && (
        <ConfirmDialog
          title={`${confirm.type === 'approve' ? 'Approve' : 'Reject'} ${confirm.ticker}?`}
          message={
            confirm.type === 'approve'
              ? `This will approve the position in ${confirm.ticker} and queue it for execution.`
              : `This will reject the position in ${confirm.ticker}. It cannot be undone.`
          }
          confirmLabel={confirm.type === 'approve' ? 'Approve' : 'Reject'}
          destructive={confirm.type === 'reject'}
          onConfirm={handleConfirm}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  )
}
