import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getPositions, getPending, approvePosition, rejectPosition } from '../api/portfolio'
import { getAlerts, getMetrics } from '../api/risk'
import { getRegime, getBriefing, runMacro } from '../api/macro'
import { getWatchlist, runScreening } from '../api/screener'
import { getOrchestratorStatus, runCycle } from '../api/orchestrator'
import axios from 'axios'
import EquityCurveChart from '../components/EquityCurveChart'
import ConfirmDialog from '../components/ConfirmDialog'

const BASE = 'http://localhost:8000'
const SEV_ORDER = { CRITICAL: 0, BREACH: 1, WARN: 2 }

const fmtMoney = (v, digits = 2, sign = false) => {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  const prefix = sign ? (n >= 0 ? '+' : '-') : ''
  return `${prefix}$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

function fmtTs(ts) {
  if (!ts) return 'Never'
  const d = new Date(ts)
  const diff = Math.round((Date.now() - d) / 60000)
  if (diff < 1) return 'Just now'
  if (diff < 60) return `${diff}m ago`
  if (diff < 1440) return `${Math.round(diff / 60)}h ago`
  return d.toLocaleDateString()
}

function buildEquityData(positions) {
  const closed = (positions || []).filter(p => p.status === 'CLOSED' && p.closed_at && p.pnl != null)
  closed.sort((a, b) => new Date(a.closed_at) - new Date(b.closed_at))
  let cum = 0
  return closed.map(p => {
    cum += Number(p.pnl)
    return { date: new Date(p.closed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }), cumulativePnl: cum }
  })
}

function AgentRow({ label, icon, lastRun, running, onRun, detail }) {
  return (
    <div className="flex items-center justify-between py-3 border-b border-surface-container-low last:border-0">
      <div className="flex items-center gap-3">
        <span className="material-symbols-outlined text-primary text-[18px]">{icon}</span>
        <div>
          <p className="text-sm font-semibold text-on-surface">{label}</p>
          <p className="text-[10px] text-on-surface-variant">{detail || fmtTs(lastRun)}</p>
        </div>
      </div>
      <button
        onClick={onRun}
        disabled={running}
        className="text-[10px] font-bold uppercase tracking-wider text-primary border border-primary px-3 py-1 rounded-lg hover:bg-primary hover:text-white transition-colors disabled:opacity-40"
      >
        {running ? 'Running…' : 'Run Now'}
      </button>
    </div>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const pendingRef = useRef(null)

  const [positions, setPositions] = useState([])
  const [pending, setPending] = useState([])
  const [alerts, setAlerts] = useState([])
  const [metrics, setMetrics] = useState(null)
  const [regime, setRegime] = useState(null)
  const [briefing, setBriefing] = useState(null)
  const [watchlist, setWatchlist] = useState([])
  const [orchStatus, setOrchStatus] = useState(null)
  const [confirm, setConfirm] = useState(null)

  // Agent running states
  const [macroRunning, setMacroRunning] = useState(false)
  const [screenerRunning, setScreenerRunning] = useState(false)
  const [researchRunning, setResearchRunning] = useState(false)
  const [cycleRunning, setCycleRunning] = useState(false)

  const loadAll = () => {
    getPositions().then(r => setPositions(r.data)).catch(() => {})
    getPending().then(r => setPending(r.data)).catch(() => {})
    getAlerts().then(r => setAlerts(r.data)).catch(() => {})
    getMetrics().then(r => setMetrics(r.data)).catch(() => {})
    getRegime().then(r => setRegime(r.data)).catch(() => {})
    getBriefing().then(r => setBriefing(r.data)).catch(() => {})
    getWatchlist().then(r => setWatchlist(r.data || [])).catch(() => {})
    getOrchestratorStatus().then(r => setOrchStatus(r.data)).catch(() => {})
  }

  useEffect(() => {
    loadAll()
    const id = setInterval(loadAll, 30_000)
    return () => clearInterval(id)
  }, [])

  const openPositions = positions.filter(p => p.status === 'OPEN')
  const todayPnl = openPositions.reduce((s, p) => s + (Number(p.pnl) || 0), 0)
  const maxDD = metrics?.max_drawdown != null ? `${(Number(metrics.max_drawdown) * 100).toFixed(2)}%` : '—'
  const portfolioValue = metrics?.portfolio_value != null
    ? fmtMoney(metrics.portfolio_value, 0)
    : '$25,000'
  const sortedAlerts = [...alerts].filter(a => !a.resolved).sort((a, b) => (SEV_ORDER[a.severity] ?? 3) - (SEV_ORDER[b.severity] ?? 3))
  const equityData = buildEquityData(positions)

  const handleAction = async () => {
    if (!confirm) return
    try {
      if (confirm.type === 'approve') await approvePosition(confirm.positionId)
      else await rejectPosition(confirm.positionId)
      loadAll()
    } catch {
      // silent
    } finally {
      setConfirm(null)
    }
  }

  const handleRunMacro = async () => {
    setMacroRunning(true)
    try { await runMacro(); loadAll() } catch { /* silent */ } finally { setMacroRunning(false) }
  }

  const handleRunScreener = async () => {
    setScreenerRunning(true)
    try { await runScreening(); loadAll() } catch { /* silent */ } finally { setScreenerRunning(false) }
  }

  const handleRunResearch = async () => {
    setResearchRunning(true)
    try { await axios.post(`${BASE}/research/run-queued`); loadAll() } catch { /* silent */ } finally { setResearchRunning(false) }
  }

  const handleRunCycle = async () => {
    setCycleRunning(true)
    try { await runCycle(); loadAll() } catch { /* silent */ } finally { setCycleRunning(false) }
  }

  // Data for system status panel
  const macroLastRun = briefing?.date ? `${briefing.date} (${regime?.regime || '—'})` : null
  const screenerLastRun = watchlist[0]?.created_at || null
  const queuedCount = watchlist.filter(w => w.queued_for_research).length
  const researchDetail = queuedCount > 0 ? `${queuedCount} ticker${queuedCount > 1 ? 's' : ''} queued` : 'No tickers queued'
  const orchLastRun = orchStatus?.last_cycle_ts || null

  return (
    <div>
      <header className="w-full flex justify-between items-center px-8 py-4 bg-transparent">
        <div className="flex items-center bg-surface-container-low px-4 py-2 rounded-full w-96 transition-all focus-within:bg-surface-container-lowest focus-within:ring-2 ring-primary ring-opacity-20">
          <span className="material-symbols-outlined text-outline text-sm">search</span>
          <input className="bg-transparent border-none focus:ring-0 text-sm w-full ml-2" placeholder="Search markets or assets..." type="text" />
        </div>
        <div className="flex items-center space-x-4">
          <button className="p-2 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-transform hover:scale-105">
            <span className="material-symbols-outlined text-on-surface-variant">notifications</span>
          </button>
          <button className="p-2 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-transform hover:scale-105">
            <span className="material-symbols-outlined text-on-surface-variant">history</span>
          </button>
          <div className="flex items-center space-x-3 ml-4 cursor-pointer hover:bg-slate-100 dark:hover:bg-slate-800 p-1 pr-3 rounded-full transition-colors">
            <img
              className="w-8 h-8 rounded-full object-cover border-2 border-surface-container-highest"
              alt="profile"
              src="https://lh3.googleusercontent.com/aida-public/AB6AXuBc9KqDV2pRQE7gJJUFB8y2SZZosBkHQvAp2smP3xc65Fq-g0KmFov2LFOLbu1D7Nediz5_QX88LPzD1KVlajp1QiO5CcWI6r5EuGFJlRgnOLT9pkNlNYC1GEVF861X9EAnL3DxscDMvcQlsZbzYgK06n4CbeVdMwVfiDyQWM8V6xQ3RY0yqF_kiIzL-iBvUIa24uGqv23y2spdeFznDEU_um8dbJVvKd6_NgHJ6L8tpIEmnIotMzM8snMX6bkOKyUPMYlKtOt0ApA"
            />
            <span className="material-symbols-outlined text-on-surface-variant">account_circle</span>
          </div>
        </div>
      </header>

      <div className="px-8 pb-12">
        <div className="flex justify-between items-end mb-6">
          <div>
            <h2 className="text-3xl font-bold font-headline text-on-surface tracking-tight">The Cockpit</h2>
            <p className="text-on-surface-variant text-sm mt-1">Real-time oversight of your AI portfolio orchestrator.</p>
          </div>
          <div className="flex space-x-2">
            <span className="bg-surface-container-high px-3 py-1 rounded text-[10px] font-bold text-on-surface-variant">MARKET: OPEN</span>
          </div>
        </div>

        {/* ── Pending Approvals Banner ─────────────────────────────────────── */}
        <div ref={pendingRef} className="mb-6">
          {pending.length > 0 ? (
            <div className="bg-yellow-50 border border-yellow-200 rounded-xl px-5 py-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <span className="relative flex h-2.5 w-2.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-yellow-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-yellow-500"></span>
                  </span>
                  <span className="text-sm font-bold text-yellow-800">
                    {pending.length} position{pending.length > 1 ? 's' : ''} awaiting your approval
                  </span>
                </div>
                <button
                  onClick={() => navigate('/portfolio')}
                  className="text-[11px] font-bold uppercase tracking-wider text-yellow-700 border border-yellow-400 px-3 py-1 rounded-lg hover:bg-yellow-100 transition-colors"
                >
                  Review Now →
                </button>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {pending.map(p => (
                  <div key={p.id} className="flex items-center gap-2 bg-white border border-yellow-200 rounded-lg px-3 py-1.5">
                    <span className="font-bold text-sm text-on-surface">{p.ticker}</span>
                    <span className="text-[10px] text-green-700 font-bold">{p.direction}</span>
                    <span className="text-[10px] text-on-surface-variant">${Number(p.dollar_size).toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
                    <span className="text-[10px] font-bold text-primary">{Number(p.conviction_score || 0).toFixed(1)}/10</span>
                    <button
                      onClick={() => setConfirm({ type: 'approve', positionId: p.id, ticker: p.ticker })}
                      className="text-green-700 hover:bg-green-100 rounded px-1.5 py-0.5 text-[10px] font-bold border border-green-300 transition-colors"
                    >✓</button>
                    <button
                      onClick={() => setConfirm({ type: 'reject', positionId: p.id, ticker: p.ticker })}
                      className="text-red-600 hover:bg-red-100 rounded px-1.5 py-0.5 text-[10px] font-bold border border-red-300 transition-colors"
                    >✗</button>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="bg-green-50 border border-green-100 rounded-xl px-5 py-3 flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-green-400 inline-block"></span>
              <span className="text-xs font-semibold text-green-700">No pending approvals</span>
            </div>
          )}
        </div>

        {/* ── KPI Strip ────────────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-6 mb-8">
          <div className="bg-surface-container-lowest p-5 rounded-xl">
            <p className="text-[10px] font-bold uppercase tracking-wider text-on-surface-variant mb-1">Portfolio Value</p>
            <h3 className="text-2xl font-bold font-headline text-on-surface">{portfolioValue}</h3>
          </div>
          <div className="bg-surface-container-lowest p-5 rounded-xl">
            <p className="text-[10px] font-bold uppercase tracking-wider text-on-surface-variant mb-1">Today P&amp;L</p>
            <h3 className={`text-2xl font-bold font-headline ${todayPnl >= 0 ? 'text-primary' : 'text-error'}`}>
              {fmtMoney(todayPnl, 2, true)}
            </h3>
          </div>
          <div className="bg-surface-container-lowest p-5 rounded-xl">
            <p className="text-[10px] font-bold uppercase tracking-wider text-on-surface-variant mb-1">Max Drawdown</p>
            <h3 className="text-2xl font-bold font-headline text-tertiary">{maxDD}</h3>
          </div>
          <div className="bg-surface-container-lowest p-5 rounded-xl">
            <p className="text-[10px] font-bold uppercase tracking-wider text-on-surface-variant mb-1">Open Positions</p>
            <h3 className="text-2xl font-bold font-headline text-on-surface">{openPositions.length}</h3>
          </div>
          <div className="bg-surface-container-lowest p-5 rounded-xl">
            <p className="text-[10px] font-bold uppercase tracking-wider text-on-surface-variant mb-1">Pending Orders</p>
            <h3 className="text-2xl font-bold font-headline text-on-surface">{pending.length}</h3>
          </div>
        </div>

        {/* ── Main Content: Equity Curve or System Status + Alerts ─────────── */}
        <div className="grid grid-cols-12 gap-8 mb-8">
          <div className="col-span-12 lg:col-span-8 bg-surface-container-lowest p-6 rounded-xl relative overflow-hidden">
            {equityData.length > 0 ? (
              <>
                <div className="flex justify-between items-start mb-6">
                  <div>
                    <h4 className="text-sm font-semibold text-on-surface font-headline">Equity Curve</h4>
                    <p className="text-xs text-on-surface-variant">Closed P&amp;L growth</p>
                  </div>
                </div>
                <EquityCurveChart data={equityData} />
                <div className="absolute inset-0 bg-gradient-to-t from-white via-transparent pointer-events-none opacity-20"></div>
              </>
            ) : (
              <>
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h4 className="text-sm font-semibold text-on-surface font-headline">System Status</h4>
                    <p className="text-xs text-on-surface-variant">Agent health &amp; manual controls</p>
                  </div>
                  <span className="material-symbols-outlined text-primary text-[18px]">hub</span>
                </div>
                <AgentRow
                  label="Macro Agent"
                  icon="language"
                  lastRun={briefing?.date ? `${briefing.date}` : null}
                  detail={macroLastRun}
                  running={macroRunning}
                  onRun={handleRunMacro}
                />
                <AgentRow
                  label="Screening Agent"
                  icon="filter_list"
                  lastRun={screenerLastRun}
                  detail={screenerLastRun ? `${watchlist.length} candidates · ${fmtTs(screenerLastRun)}` : null}
                  running={screenerRunning}
                  onRun={handleRunScreener}
                />
                <AgentRow
                  label="Research Queue"
                  icon="query_stats"
                  lastRun={null}
                  detail={researchDetail}
                  running={researchRunning}
                  onRun={handleRunResearch}
                />
                <AgentRow
                  label="Orchestrator Cycle"
                  icon="memory"
                  lastRun={orchLastRun}
                  detail={orchLastRun ? `${orchStatus?.mode || 'SUPERVISED'} · ${fmtTs(orchLastRun)}` : null}
                  running={cycleRunning}
                  onRun={handleRunCycle}
                />
              </>
            )}
          </div>

          <div className="col-span-12 lg:col-span-4 bg-surface-container-lowest p-6 rounded-xl">
            <h4 className="text-sm font-semibold text-on-surface font-headline mb-4">Risk Alerts</h4>
            <div className="space-y-3">
              {sortedAlerts.length === 0 && (
                <p className="text-xs text-on-surface-variant">No active alerts.</p>
              )}
              {sortedAlerts.slice(0, 3).map(a => (
                <div key={a.id} className="flex p-3 bg-surface-container-low rounded-lg relative overflow-hidden">
                  <div className={`absolute left-0 top-0 bottom-0 w-1 ${a.severity === 'CRITICAL' ? 'bg-error' : a.severity === 'BREACH' ? 'bg-tertiary' : 'bg-tertiary-container'}`}></div>
                  <div className="ml-2">
                    <p className={`text-[10px] font-bold uppercase tracking-wider ${a.severity === 'CRITICAL' ? 'text-error' : 'text-tertiary'}`}>{a.severity}</p>
                    <p className="text-xs font-semibold text-on-surface">{a.trigger}</p>
                    <p className="text-[10px] text-on-surface-variant mt-1">{a.ticker || 'Portfolio'}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* ── Bottom Cards ─────────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-8">
          <div className="bg-surface-container-lowest p-6 rounded-xl">
            <h4 className="text-[10px] font-bold uppercase tracking-widest text-on-surface-variant mb-4">Macro Regime</h4>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-2xl font-bold font-headline text-on-surface">{regime?.regime || '—'}</p>
                <p className="text-xs text-on-surface-variant">Score: {regime?.regime_score != null ? Number(regime.regime_score).toFixed(1) : '—'}</p>
              </div>
              <div className="relative w-16 h-16 flex items-center justify-center">
                <svg className="w-full h-full transform -rotate-90">
                  <circle className="text-surface-container-high" cx="32" cy="32" fill="transparent" r="28" stroke="currentColor" strokeWidth="4"></circle>
                  <circle
                    className="text-primary"
                    cx="32" cy="32" fill="transparent" r="28"
                    stroke="currentColor"
                    strokeDasharray="175.9"
                    strokeDashoffset={regime?.regime_score != null ? 175.9 - (Number(regime.regime_score) / 100) * 175.9 : 60}
                    strokeWidth="4"
                  ></circle>
                </svg>
                <span className="absolute text-[10px] font-bold">{regime?.regime_score != null ? Number(regime.regime_score).toFixed(0) : '—'}</span>
              </div>
            </div>
          </div>

          <div className="bg-surface-container-lowest p-6 rounded-xl">
            <h4 className="text-[10px] font-bold uppercase tracking-widest text-on-surface-variant mb-4">Last Screener</h4>
            <div className="flex justify-between items-center mb-4">
              <span className="text-2xl font-bold font-headline">{watchlist.length || 0} Candidates</span>
            </div>
            <div className="flex space-x-2 flex-wrap">
              {watchlist.slice(0, 5).map(w => (
                <span key={w.id} className="px-2 py-1 bg-surface-container-low text-[10px] font-bold rounded">
                  {w.ticker}
                </span>
              ))}
            </div>
          </div>

          <div className="bg-surface-container-lowest p-6 rounded-xl relative overflow-hidden">
            <div className="absolute top-0 right-0 p-4 opacity-10">
              <span className="material-symbols-outlined text-6xl">memory</span>
            </div>
            <h4 className="text-[10px] font-bold uppercase tracking-widest text-on-surface-variant mb-4">Orchestrator</h4>
            <div className="space-y-1 mb-4">
              <p className="text-xs font-medium">Mode: <span className="text-primary font-bold">{orchStatus?.mode || 'SUPERVISED'}</span></p>
              <p className="text-xs font-medium text-on-surface-variant">Last cycle: {fmtTs(orchStatus?.last_cycle_ts)}</p>
            </div>
            <button
              onClick={handleRunCycle}
              disabled={cycleRunning}
              className="w-full primary-gradient text-white py-2.5 rounded-lg text-xs font-bold tracking-wider hover:opacity-90 transition-all shadow-lg shadow-primary/10 disabled:opacity-60"
            >
              {cycleRunning ? 'RUNNING…' : 'RUN CYCLE NOW'}
            </button>
          </div>
        </div>
      </div>

      <div className="fixed bottom-8 right-8 pointer-events-none">
        <div className="w-32 h-32 bg-primary opacity-5 blur-3xl rounded-full"></div>
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
          onConfirm={handleAction}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  )
}
