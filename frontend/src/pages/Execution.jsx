import { useEffect, useRef, useState } from 'react'
import { cancelOrder, getFills, getExecutionStatus, getOrders, runExecutionCycle } from '../api/execution'
import ConfirmDialog from '../components/ConfirmDialog'

const STATUS_COLORS = {
  SUBMITTED: 'text-primary',
  PARTIAL: 'text-primary',
  FILLED: 'text-secondary',
  TIMEOUT: 'text-error',
  ERROR: 'text-error',
  CANCELLED: 'text-outline',
  PENDING: 'text-outline',
}

function formatTs(ts) {
  if (!ts) return '—'
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export default function Execution() {
  const [orders, setOrders] = useState([])
  const [fills, setFills] = useState([])
  const [execStatus, setExecStatus] = useState(null)
  const [running, setRunning] = useState(false)
  const [confirm, setConfirm] = useState(null)
  const autoCycleRef = useRef(false)

  const load = () => {
    getOrders().then(r => setOrders(r.data || [])).catch(() => {})
    getFills().then(r => setFills(r.data || [])).catch(() => {})
    getExecutionStatus()
      .then(r => {
        const data = r?.data || {}
        const connected = data.connected ?? data.ibkr_connected ?? false
        const env = data.env ?? (data.is_paper == null ? null : (data.is_paper ? 'paper' : 'live'))
        setExecStatus({ ...data, connected, env })
      })
      .catch(() => {})
  }

  useEffect(() => {
    load()
    const pollId = setInterval(load, 10_000)
    const cycleId = setInterval(async () => {
      if (autoCycleRef.current) return
      autoCycleRef.current = true
      try {
        await runExecutionCycle()
      } catch {
        // silent
      } finally {
        autoCycleRef.current = false
        load()
      }
    }, 20_000)
    return () => {
      clearInterval(pollId)
      clearInterval(cycleId)
    }
  }, [])

  const activeCount = orders.filter(o => ['SUBMITTED', 'PARTIAL', 'PENDING'].includes(o.status)).length
  const filledToday = fills.filter(f => {
    const d = new Date(f.fill_time)
    const now = new Date()
    return d.toDateString() === now.toDateString()
  }).length
  const cancelledCount = orders.filter(o => o.status === 'CANCELLED').length

  const handleCancel = async () => {
    if (!confirm) return
    try {
      await cancelOrder(confirm.id)
      load()
    } catch {
      // silent
    } finally {
      setConfirm(null)
    }
  }

  const handleRunCycle = async () => {
    setRunning(true)
    try {
      await runExecutionCycle()
      load()
    } catch {
      // silent
    } finally {
      setRunning(false)
    }
  }

  return (
    <div>
      <header className="w-full flex justify-between items-center px-8 py-4 bg-transparent">
        <div className="flex items-center bg-surface-container-low px-4 py-2 rounded-xl w-96 group focus-within:bg-surface-container-lowest transition-all">
          <span className="material-symbols-outlined text-outline mr-3">search</span>
          <input className="bg-transparent border-none focus:ring-0 text-sm w-full placeholder:text-outline" placeholder="Search orders, tickers, or fills..." type="text" />
        </div>
        <div className="flex items-center space-x-6">
          <button className="relative hover:bg-surface-container-high p-2 rounded-lg transition-all scale-100 hover:scale-110">
            <span className="material-symbols-outlined text-on-surface-variant">notifications</span>
            <span className="absolute top-2 right-2 w-2 h-2 bg-error rounded-full border-2 border-surface"></span>
          </button>
          <button className="hover:bg-surface-container-high p-2 rounded-lg transition-all scale-100 hover:scale-110">
            <span className="material-symbols-outlined text-on-surface-variant">history</span>
          </button>
          <div className="flex items-center space-x-3 ml-4 cursor-pointer hover:bg-surface-container-high p-1 pr-3 rounded-full transition-all">
            <span className="material-symbols-outlined text-[32px] text-primary" style={{ fontVariationSettings: "'FILL' 1" }}>account_circle</span>
            <div className="flex flex-col">
              <span className="text-xs font-bold leading-none">Trader.A1</span>
              <span className="text-[10px] text-outline">Terminal View</span>
            </div>
          </div>
        </div>
      </header>

      <div className="px-8 py-6 space-y-8">
        <div className="flex items-end justify-between">
          <div>
            <h2 className="text-2xl font-bold text-on-surface tracking-tight mb-2">Execution — IBKR Orders &amp; Fills</h2>
            <div className="flex items-center space-x-6">
              <div className="flex items-center space-x-2">
                <span className="text-[11px] font-bold tracking-wider text-on-surface-variant uppercase">Active Orders:</span>
                <span className="text-lg font-bold text-primary">{activeCount}</span>
              </div>
              <div className="h-4 w-[1px] bg-outline-variant opacity-30"></div>
              <div className="flex items-center space-x-2">
                <span className="text-[11px] font-bold tracking-wider text-on-surface-variant uppercase">Filled Today:</span>
                <span className="text-lg font-bold text-on-surface">{filledToday}</span>
              </div>
              <div className="h-4 w-[1px] bg-outline-variant opacity-30"></div>
              <div className="flex items-center space-x-2">
                <span className="text-[11px] font-bold tracking-wider text-on-surface-variant uppercase">Cancelled:</span>
                <span className="text-lg font-bold text-on-surface opacity-40">{cancelledCount}</span>
              </div>
            </div>
          </div>
          <button
            onClick={handleRunCycle}
            disabled={running}
            className="signature-gradient text-on-primary px-6 py-3 rounded-xl flex items-center space-x-2 shadow-lg shadow-primary/20 hover:opacity-90 transition-opacity disabled:opacity-60"
          >
            <span className="material-symbols-outlined text-[20px]" style={{ fontVariationSettings: "'FILL' 1" }}>play_arrow</span>
            <span className="text-sm font-bold tracking-wide uppercase">{running ? 'Running…' : 'Run Cycle'}</span>
          </button>
        </div>

        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <span className="material-symbols-outlined text-primary">list_alt</span>
              <h3 className="text-sm font-bold tracking-wider text-on-surface-variant uppercase">Live Orders</h3>
            </div>
            <span className="text-xs text-outline">Real-time IBKR WebSocket Active</span>
          </div>
          <div className="bg-surface-container-lowest rounded-xl overflow-hidden">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-surface-container-low">
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Ticker</th>
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Dir</th>
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Type</th>
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Req Qty</th>
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Filled</th>
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Status</th>
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Submitted</th>
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-container-low">
                {orders.length === 0 && (
                  <tr>
                    <td colSpan="8" className="px-6 py-6 text-sm text-on-surface-variant text-center">No orders yet</td>
                  </tr>
                )}
                {orders.map(o => (
                  <tr key={o.id} className="group hover:bg-surface-container-high transition-colors">
                    <td className="px-6 py-5">
                      <div className="flex items-center space-x-3">
                        <div className="w-8 h-8 rounded-lg bg-surface-container-low flex items-center justify-center font-bold text-xs">
                          {o.ticker?.[0] || 'A'}
                        </div>
                        <span className="font-bold text-sm">{o.ticker}</span>
                      </div>
                    </td>
                    <td className="px-6 py-5">
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded-sm bg-primary-container text-on-primary-container">
                        {o.direction === 'LONG' ? 'BUY' : 'SELL'}
                      </span>
                    </td>
                    <td className="px-6 py-5 text-sm font-medium">{o.order_type}</td>
                    <td className="px-6 py-5 text-sm font-semibold">{Number(o.requested_qty).toFixed(0)}</td>
                    <td className="px-6 py-5 text-sm font-medium text-outline">{Number(o.total_filled_qty || 0).toFixed(0)}</td>
                    <td className="px-6 py-5">
                      <div className={`flex items-center font-bold text-[10px] tracking-wider ${STATUS_COLORS[o.status] || 'text-outline'}`}>
                        <span className={`w-1.5 h-1.5 rounded-full mr-2 ${o.status === 'SUBMITTED' ? 'bg-primary animate-pulse' : o.status === 'FILLED' ? 'bg-secondary' : 'bg-outline'}`}></span>
                        {o.status}
                      </div>
                    </td>
                    <td className="px-6 py-5 text-sm text-on-surface-variant">{formatTs(o.submitted_at)}</td>
                    <td className="px-6 py-5 text-right">
                      {['SUBMITTED', 'PARTIAL', 'PENDING'].includes(o.status) ? (
                        <button
                          onClick={() => setConfirm({ id: o.id, ticker: o.ticker })}
                          className="text-error hover:bg-error-container px-3 py-1.5 rounded-lg text-xs font-bold transition-all uppercase tracking-tighter"
                        >
                          Cancel
                        </button>
                      ) : (
                        <span className="text-outline text-[10px] font-bold uppercase">Locked</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <span className="material-symbols-outlined text-tertiary">receipt_long</span>
              <h3 className="text-sm font-bold tracking-wider text-on-surface-variant uppercase">Recent Fills</h3>
            </div>
          </div>
          <div className="bg-surface-container-lowest rounded-xl overflow-hidden relative">
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-primary"></div>
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-surface-container-low border-l-4 border-transparent">
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest pl-10">Ticker</th>
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Fill Qty</th>
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Fill Price</th>
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Slippage</th>
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Commission</th>
                  <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Time</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-container-low">
                {fills.length === 0 && (
                  <tr>
                    <td colSpan="6" className="px-6 py-6 text-sm text-on-surface-variant text-center">No fills yet</td>
                  </tr>
                )}
                {fills.map(f => {
                  const slip = f.slippage_bps != null ? Number(f.slippage_bps) : null
                  const slipClass = slip != null && slip > 0 ? 'text-tertiary' : 'text-secondary'
                  return (
                    <tr key={f.id} className="group hover:bg-surface-container-high transition-colors">
                      <td className="px-6 py-5 pl-10"><span className="font-bold text-sm">{f.ticker}</span></td>
                      <td className="px-6 py-5 text-sm font-semibold">{Number(f.fill_qty).toFixed(0)}</td>
                      <td className="px-6 py-5 text-sm font-mono font-medium">${Number(f.fill_price).toFixed(2)}</td>
                      <td className="px-6 py-5">
                        <div className={`flex items-center font-bold text-xs ${slipClass}`}>
                          {slip != null ? `${slip.toFixed(1)} bps` : '—'}
                          <span className="material-symbols-outlined text-[14px] ml-1">{slip != null && slip > 0 ? 'trending_up' : 'trending_down'}</span>
                        </div>
                      </td>
                      <td className="px-6 py-5 text-sm text-on-surface-variant font-medium">
                        {f.commission != null ? `$${Number(f.commission).toFixed(2)}` : '—'}
                      </td>
                      <td className="px-6 py-5 text-right text-sm text-outline">{formatTs(f.fill_time)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 pt-6">
          <div className="bg-surface-container-low p-6 rounded-xl space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold tracking-wider text-outline uppercase">API Connectivity</span>
              <span className={`w-2 h-2 rounded-full ${execStatus?.connected ? 'bg-secondary' : 'bg-error'}`}></span>
            </div>
            <div className="flex items-end justify-between">
              <div>
                <p className="text-sm font-bold">IBKR Gateway</p>
                <p className="text-xs text-on-surface-variant">{execStatus?.connected ? 'Connected' : 'Disconnected'}</p>
              </div>
              <span className="material-symbols-outlined text-on-surface-variant">lan</span>
            </div>
          </div>
          <div className="bg-surface-container-low p-6 rounded-xl space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold tracking-wider text-outline uppercase">Engine Status</span>
              <span className="w-2 h-2 rounded-full bg-secondary"></span>
            </div>
            <div className="flex items-end justify-between">
              <div>
                <p className="text-sm font-bold">Auto-Liquidator</p>
                <p className="text-xs text-on-surface-variant">Active Monitoring</p>
              </div>
              <span className="material-symbols-outlined text-on-surface-variant">verified_user</span>
            </div>
          </div>
          <div className="bg-surface-container-low p-6 rounded-xl space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-[10px] font-bold tracking-wider text-outline uppercase">Trading Mode</span>
              <span className="w-2 h-2 rounded-full bg-on-primary-fixed"></span>
            </div>
            <div className="flex items-end justify-between">
              <div>
                <p className="text-sm font-bold">{execStatus?.env === 'paper' ? 'Paper' : 'Live'}</p>
                <p className="text-xs text-on-surface-variant">{execStatus?.env ? `${execStatus.env.toUpperCase()} ENV` : 'Unknown'}</p>
              </div>
              <span className="material-symbols-outlined text-on-surface-variant">pan_tool</span>
            </div>
          </div>
        </div>
      </div>

      {confirm && (
        <ConfirmDialog
          title={`Cancel ${confirm.ticker} order?`}
          message="This will cancel the live IBKR order. A new order will not be submitted unless you re-approve the position."
          confirmLabel="Cancel Order"
          destructive
          onConfirm={handleCancel}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  )
}
