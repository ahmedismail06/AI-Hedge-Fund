import { useEffect, useState } from 'react'
import { getAlerts, getMetrics, resolveAlert, runNightlyMetrics, runRiskMonitor } from '../api/risk'

const fmt = (v, digits = 2) => (v == null ? '—' : Number(v).toFixed(digits))

export default function Risk() {
  const [alerts, setAlerts] = useState([])
  const [metrics, setMetrics] = useState(null)
  const [runningMetrics, setRunningMetrics] = useState(false)
  const [runningMonitor, setRunningMonitor] = useState(false)

  const loadAlerts = () => {
    getAlerts().then(r => setAlerts(r.data || [])).catch(() => {})
  }

  const loadMetrics = () => {
    getMetrics().then(r => setMetrics(r.data || r)).catch(() => {})
  }

  useEffect(() => {
    loadAlerts()
    loadMetrics()
    const id = setInterval(loadAlerts, 30_000)
    return () => clearInterval(id)
  }, [])

  const SEV_ORDER = { CRITICAL: 0, BREACH: 1, WARN: 2 }
  const sortedAlerts = [...alerts].sort((a, b) => (SEV_ORDER[a.severity] ?? 3) - (SEV_ORDER[b.severity] ?? 3))
  const visibleAlerts = sortedAlerts.filter(a => !a.resolved)
  const criticalCount = alerts.filter(a => !a.resolved && a.severity === 'CRITICAL').length

  const handleResolve = async (id) => {
    try {
      await resolveAlert(id)
      loadAlerts()
    } catch {
      // silent
    }
  }

  const handleRunMetrics = async () => {
    setRunningMetrics(true)
    try {
      await runNightlyMetrics()
      loadMetrics()
    } catch {
      // silent
    } finally {
      setRunningMetrics(false)
    }
  }

  const handleRunMonitor = async () => {
    setRunningMonitor(true)
    try {
      await runRiskMonitor()
      loadAlerts()
    } catch {
      // silent
    } finally {
      setRunningMonitor(false)
    }
  }

  return (
    <div>
      <header className="w-[calc(100%-220px)] ml-[220px] fixed top-0 bg-surface z-40">
        <div className="flex justify-between items-center px-8 py-4 w-full">
          <div className="flex-1 max-w-md">
            <div className="relative group">
              <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline">search</span>
              <input className="w-full bg-surface-container-low border-none rounded-full py-2 pl-10 pr-4 text-sm focus:ring-2 focus:ring-primary/20 placeholder:text-outline-variant" placeholder="Search risk parameters..." type="text" />
            </div>
          </div>
          <div className="flex items-center space-x-6 ml-8">
            <button className="relative hover:bg-surface-container-low p-2 rounded-full transition-all hover:scale-110">
              <span className="material-symbols-outlined text-on-surface-variant">notifications</span>
              <span className="absolute top-2 right-2 w-2 h-2 bg-error rounded-full border-2 border-surface"></span>
            </button>
            <button className="hover:bg-surface-container-low p-2 rounded-full transition-all hover:scale-110">
              <span className="material-symbols-outlined text-on-surface-variant">history</span>
            </button>
            <div className="flex items-center space-x-3 ml-2 border-l border-outline-variant pl-6">
              <div className="text-right">
                <p className="text-xs font-bold text-on-surface">Alex Chen</p>
                <p className="text-[10px] text-outline uppercase font-medium">Head of Risk</p>
              </div>
              <span className="material-symbols-outlined text-[32px] text-on-surface-variant">account_circle</span>
            </div>
          </div>
        </div>
      </header>

      <main className="ml-[0px] pt-[72px] min-h-screen">
        {criticalCount > 0 && (
          <div className="bg-error text-on-error px-8 py-4 flex items-center justify-between shadow-lg relative overflow-hidden">
            <div className="flex items-center space-x-3">
              <span className="material-symbols-outlined text-[24px]" style={{ fontVariationSettings: "'FILL' 1" }}>warning</span>
              <span className="font-headline font-bold text-lg tracking-tight uppercase">{criticalCount} unresolved CRITICAL alert — autonomous mode is blocked</span>
            </div>
            <div className="flex items-center space-x-4">
              <span className="text-sm font-medium opacity-90">Protocol: Manual Intervention Required</span>
              <button className="bg-surface-container-lowest text-error px-4 py-1.5 rounded-full text-xs font-bold uppercase tracking-wider hover:bg-on-error transition-colors">Emergency Dashboard</button>
            </div>
          </div>
        )}

        <div className="px-8 py-8 space-y-10 max-w-[1600px] mx-auto">
          <div className="flex items-end justify-between">
            <div>
              <h2 className="text-3xl font-bold font-headline text-on-surface tracking-tight">Risk Monitoring</h2>
              <p className="text-on-surface-variant mt-1 text-sm">Real-time exposure management and threshold auditing.</p>
            </div>
            <div className="flex space-x-3">
              <button
                onClick={handleRunMetrics}
                disabled={runningMetrics}
                className="px-5 py-2.5 bg-surface-container-high text-on-surface font-semibold text-sm rounded-full hover:bg-surface-dim transition-all flex items-center space-x-2 disabled:opacity-60"
              >
                <span className="material-symbols-outlined text-[18px]">refresh</span>
                <span>{runningMetrics ? 'Running Metrics…' : 'Run Nightly Metrics'}</span>
              </button>
              <button
                onClick={handleRunMonitor}
                disabled={runningMonitor}
                className="px-5 py-2.5 signature-gradient text-on-primary font-semibold text-sm rounded-full shadow-lg shadow-primary/20 hover:scale-[1.02] transition-all flex items-center space-x-2 disabled:opacity-60"
              >
                <span className="material-symbols-outlined text-[18px]">bolt</span>
                <span>{runningMonitor ? 'Running Monitor…' : 'Run Risk Monitor'}</span>
              </button>
            </div>
          </div>

          <section className="space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="label-sm text-outline uppercase font-bold tracking-[0.1em] text-[11px]">Active Risk Alerts</h3>
              <span className="text-xs text-outline-variant font-medium">Auto-refreshing every 30s</span>
            </div>
            <div className="bg-surface-container-lowest rounded-xl overflow-hidden">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-surface-container-low">
                    <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Severity</th>
                    <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Ticker</th>
                    <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Tier</th>
                    <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Trigger</th>
                    <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest">Created</th>
                    <th className="px-6 py-4 text-[11px] font-bold text-on-surface-variant uppercase tracking-widest text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-surface-container">
                  {visibleAlerts.length === 0 && (
                    <tr>
                      <td colSpan="6" className="px-6 py-6 text-sm text-on-surface-variant text-center">No unresolved alerts</td>
                    </tr>
                  )}
                  {visibleAlerts.map(a => (
                    <tr key={a.id} className="group hover:bg-surface-container-low transition-colors">
                      <td className="px-6 py-6 flex items-center space-x-3">
                        <div className={`w-1.5 h-6 rounded-full ${a.severity === 'CRITICAL' ? 'bg-error' : a.severity === 'BREACH' ? 'bg-tertiary' : 'bg-tertiary-container'}`}></div>
                        <span className={`text-xs font-bold px-2 py-1 rounded ${a.severity === 'CRITICAL' ? 'text-error bg-error-container/30' : a.severity === 'BREACH' ? 'text-tertiary bg-tertiary-fixed/30' : 'text-tertiary-container bg-tertiary-fixed/20'}`}>
                          {a.severity}
                        </span>
                      </td>
                      <td className="px-6 py-6 font-bold text-on-surface">{a.ticker || '—'}</td>
                      <td className="px-6 py-6 text-sm text-on-surface-variant font-medium">{a.tier || '—'}</td>
                      <td className="px-6 py-6 text-sm text-on-surface font-semibold">{a.trigger}</td>
                      <td className="px-6 py-6 text-sm text-on-surface-variant">{a.created_at ? new Date(a.created_at).toLocaleTimeString() : '—'}</td>
                      <td className="px-6 py-6 text-right">
                        <button
                          onClick={() => handleResolve(a.id)}
                          className={`px-4 py-1.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${
                            a.severity === 'CRITICAL'
                              ? 'bg-error text-on-error'
                              : a.severity === 'BREACH'
                              ? 'bg-tertiary text-on-tertiary'
                              : 'bg-surface-container-highest text-on-surface-variant'
                          }`}
                        >
                          Resolve
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="space-y-4">
            <h3 className="label-sm text-outline uppercase font-bold tracking-[0.1em] text-[11px]">System Portfolio Metrics</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
              <div className="bg-surface-container-lowest p-6 rounded-2xl flex flex-col justify-between group hover:scale-[1.02] transition-transform cursor-default">
                <div className="flex justify-between items-start">
                  <span className="text-[11px] font-bold text-outline uppercase tracking-wider">Sharpe Ratio</span>
                  <span className="material-symbols-outlined text-primary text-[20px]">trending_up</span>
                </div>
                <div className="mt-4">
                  <h4 className="text-3xl font-headline font-bold text-on-surface">{fmt(metrics?.sharpe_ratio)}</h4>
                  <div className="flex items-center space-x-1 mt-1">
                    <span className="text-[10px] font-bold text-secondary uppercase tracking-tight">+0.05 vs LW</span>
                  </div>
                </div>
              </div>
              <div className="bg-surface-container-lowest p-6 rounded-2xl flex flex-col justify-between group hover:scale-[1.02] transition-transform cursor-default">
                <div className="flex justify-between items-start">
                  <span className="text-[11px] font-bold text-outline uppercase tracking-wider">Sortino Ratio</span>
                  <span className="material-symbols-outlined text-secondary text-[20px]">equalizer</span>
                </div>
                <div className="mt-4">
                  <h4 className="text-3xl font-headline font-bold text-on-surface">{fmt(metrics?.sortino_ratio)}</h4>
                  <div className="flex items-center space-x-1 mt-1">
                    <span className="text-[10px] font-bold text-outline uppercase tracking-tight">Benchmark: 1.50</span>
                  </div>
                </div>
              </div>
              <div className="bg-surface-container-lowest p-6 rounded-2xl flex flex-col justify-between group hover:scale-[1.02] transition-transform cursor-default border-l-4 border-error/20">
                <div className="flex justify-between items-start">
                  <span className="text-[11px] font-bold text-outline uppercase tracking-wider">Max Drawdown</span>
                  <span className="material-symbols-outlined text-error text-[20px]">monitoring</span>
                </div>
                <div className="mt-4">
                  <h4 className="text-3xl font-headline font-bold text-error">{metrics?.max_drawdown != null ? `${(Number(metrics.max_drawdown) * 100).toFixed(2)}%` : '—'}</h4>
                  <div className="flex items-center space-x-1 mt-1">
                    <span className="text-[10px] font-bold text-error uppercase tracking-tight">Warning Threshold: -3.5%</span>
                  </div>
                </div>
              </div>
              <div className="bg-surface-container-lowest p-6 rounded-2xl flex flex-col justify-between group hover:scale-[1.02] transition-transform cursor-default">
                <div className="flex justify-between items-start">
                  <span className="text-[11px] font-bold text-outline uppercase tracking-wider">VaR 95%</span>
                  <span className="material-symbols-outlined text-primary text-[20px]">analytics</span>
                </div>
                <div className="mt-4">
                  <h4 className="text-3xl font-headline font-bold text-on-surface">{metrics?.var_95 != null ? `${(Number(metrics.var_95) * 100).toFixed(2)}%` : '—'}</h4>
                  <div className="flex items-center space-x-1 mt-1">
                    <span className="text-[10px] font-bold text-on-surface-variant uppercase tracking-tight">Daily Horizon</span>
                  </div>
                </div>
              </div>
              <div className="bg-surface-container-lowest p-6 rounded-2xl flex flex-col justify-between group hover:scale-[1.02] transition-transform cursor-default">
                <div className="flex justify-between items-start">
                  <span className="text-[11px] font-bold text-outline uppercase tracking-wider">Portfolio Beta</span>
                  <span className="material-symbols-outlined text-outline text-[20px]">schema</span>
                </div>
                <div className="mt-4">
                  <h4 className="text-3xl font-headline font-bold text-on-surface">{fmt(metrics?.beta)}</h4>
                  <div className="flex items-center space-x-1 mt-1">
                    <span className="text-[10px] font-bold text-on-surface-variant uppercase tracking-tight">Target Range: 0.5 - 0.8</span>
                  </div>
                </div>
              </div>
              <div className="bg-surface-container-lowest p-6 rounded-2xl flex flex-col justify-between group hover:scale-[1.02] transition-transform cursor-default">
                <div className="flex justify-between items-start">
                  <span className="text-[11px] font-bold text-outline uppercase tracking-wider">Calmar Ratio</span>
                  <span className="material-symbols-outlined text-secondary text-[20px]">radar</span>
                </div>
                <div className="mt-4">
                  <h4 className="text-3xl font-headline font-bold text-on-surface">{fmt(metrics?.calmar_ratio)}</h4>
                  <div className="flex items-center space-x-1 mt-1">
                    <span className="text-[10px] font-bold text-on-surface-variant uppercase tracking-tight">System Health: Optimal</span>
                  </div>
                </div>
              </div>
              <div className="bg-surface-container-lowest p-6 rounded-2xl flex flex-col justify-between group hover:scale-[1.02] transition-transform cursor-default">
                <div className="flex justify-between items-start">
                  <span className="text-[11px] font-bold text-outline uppercase tracking-wider">Gross Exp.</span>
                  <span className="material-symbols-outlined text-primary text-[20px]">full_stacked_bar_chart</span>
                </div>
                <div className="mt-4">
                  <h4 className="text-3xl font-headline font-bold text-on-surface">{metrics?.gross_exposure != null ? `${(Number(metrics.gross_exposure) * 100).toFixed(1)}%` : '—'}</h4>
                  <div className="flex items-center space-x-1 mt-1">
                    <div className="w-full h-1 bg-surface-container rounded-full overflow-hidden">
                      <div className="w-[45%] h-full signature-gradient"></div>
                    </div>
                  </div>
                </div>
              </div>
              <div className="bg-surface-container-lowest p-6 rounded-2xl flex flex-col justify-between group hover:scale-[1.02] transition-transform cursor-default">
                <div className="flex justify-between items-start">
                  <span className="text-[11px] font-bold text-outline uppercase tracking-wider">Net Exp.</span>
                  <span className="material-symbols-outlined text-primary text-[20px]">layers</span>
                </div>
                <div className="mt-4">
                  <h4 className="text-3xl font-headline font-bold text-on-surface">{metrics?.net_exposure != null ? `${(Number(metrics.net_exposure) * 100).toFixed(1)}%` : '—'}</h4>
                  <div className="flex items-center space-x-1 mt-1">
                    <div className="w-full h-1 bg-surface-container rounded-full overflow-hidden">
                      <div className="w-[20%] h-full signature-gradient"></div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div className="lg:col-span-2 bg-surface-container-lowest p-8 rounded-3xl relative overflow-hidden">
              <div className="flex justify-between items-center mb-8">
                <h4 className="font-headline font-bold text-lg text-on-surface">Stress Test Scenarios</h4>
                <button className="text-primary font-bold text-xs uppercase tracking-wider">Configure Parameters</button>
              </div>
              <div className="space-y-6">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-on-surface">S&amp;P 500 Drop (-10%)</span>
                  <span className="text-sm font-bold text-error">-4.21% Portfolio Impact</span>
                </div>
                <div className="w-full h-2 bg-surface-container rounded-full">
                  <div className="w-[42%] h-full bg-error rounded-full"></div>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-on-surface">Oil Price Spike (+25%)</span>
                  <span className="text-sm font-bold text-tertiary">+1.12% Portfolio Impact</span>
                </div>
                <div className="w-full h-2 bg-surface-container rounded-full">
                  <div className="w-[11%] h-full bg-tertiary rounded-full"></div>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-on-surface">USD Liquidity Crunch</span>
                  <span className="text-sm font-bold text-error">-2.88% Portfolio Impact</span>
                </div>
                <div className="w-full h-2 bg-surface-container rounded-full">
                  <div className="w-[28%] h-full bg-error rounded-full"></div>
                </div>
              </div>
            </div>
            <div className="bg-on-surface p-8 rounded-3xl text-surface relative overflow-hidden group">
              <div className="relative z-10">
                <h4 className="font-headline font-bold text-lg mb-2">Automated Risk Engine</h4>
                <p className="text-surface/60 text-xs mb-6 leading-relaxed">The AI has proposed 3 rebalancing actions to offset the current critical breach.</p>
                <div className="space-y-4">
                  <div className="p-4 bg-surface/10 rounded-xl border border-surface/10">
                    <p className="text-[10px] uppercase font-bold text-primary-fixed mb-1">PROPOSED ACTION</p>
                    <p className="text-sm font-semibold">Hedge equity exposure via index put spreads</p>
                  </div>
                  <div className="p-4 bg-surface/10 rounded-xl border border-surface/10">
                    <p className="text-[10px] uppercase font-bold text-primary-fixed mb-1">PROPOSED ACTION</p>
                    <p className="text-sm font-semibold">Reduce gross exposure by 12%</p>
                  </div>
                </div>
                <button className="w-full mt-8 py-3 bg-primary text-on-primary font-bold text-xs uppercase tracking-widest rounded-xl hover:brightness-110 transition-all">Review &amp; Execute All</button>
              </div>
              <div className="absolute inset-0 opacity-10 bg-[radial-gradient(circle_at_top_right,_var(--tw-gradient-stops))] from-primary via-transparent to-transparent"></div>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
