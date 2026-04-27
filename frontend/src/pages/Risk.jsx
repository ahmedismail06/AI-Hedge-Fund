import { useState, useEffect, useCallback } from 'react';
import { getAlerts, getCriticalAlerts, resolveAlert, getMetrics, getMetricsHistory, runRiskMonitor, runNightlyMetrics } from '../api/risk';
import { getRegime } from '../api/macro';
import RiskAlert from '../components/RiskAlert';
import StatCard from '../components/StatCard';
import { BarChart, Bar, XAxis, YAxis, Tooltip as RTooltip, ResponsiveContainer, ReferenceLine } from 'recharts';

const STOP_TIERS = {
  normal:  { tier1: -8,  tier2: -15, tier3: -20 },
  riskOff: { tier1: -5,  tier2: -10, tier3: -15 },
};

const METRIC_META = {
  sharpe_ratio:         { label: 'Sharpe Ratio',          plain: 'Risk-Adjusted Return',    tip: 'How much return earned per unit of risk. Above 1.0 is good; above 2.0 is excellent.' },
  sortino_ratio:        { label: 'Sortino Ratio',         plain: 'Downside Risk Ratio',     tip: 'Like Sharpe, but only penalises losing periods — not all volatility.' },
  var_95:               { label: 'VaR 95%',               plain: 'Worst Expected Day (95%)',tip: 'On a bad day (1-in-20 probability), the portfolio could lose this much.' },
  max_drawdown:         { label: 'Max Drawdown',          plain: 'Worst Losing Streak',     tip: 'The largest peak-to-trough loss since inception.' },
  calmar_ratio:         { label: 'Calmar Ratio',          plain: 'Return vs. Drawdown',     tip: 'Compares annual return to worst drawdown. Higher is better.' },
  beta:                 { label: 'Beta',                  plain: 'Market Sensitivity',      tip: 'How much the portfolio moves relative to S&P 500. 1.0 = moves in lockstep.' },
  annualized_return:    { label: 'Annualized Return',     plain: 'Yearly Return Rate',      tip: 'Compound annual growth rate of the portfolio.' },
  win_rate:             { label: 'Win Rate',              plain: '% of Winning Trades',     tip: 'What fraction of closed trades ended in a profit.' },
};

function metricStatus(key, val) {
  if (val == null) return 'neutral';
  if (key === 'sharpe_ratio' || key === 'sortino_ratio') return val >= 1 ? 'ok' : val >= 0.5 ? 'warn' : 'critical';
  if (key === 'max_drawdown') return val > -10 ? 'ok' : val > -20 ? 'warn' : 'critical';
  if (key === 'var_95') return val > -5 ? 'ok' : val > -10 ? 'warn' : 'critical';
  if (key === 'beta') return Math.abs(val) < 0.8 ? 'ok' : Math.abs(val) < 1.2 ? 'warn' : 'critical';
  return 'neutral';
}

function fmtMetric(key, val) {
  if (val == null) return '—';
  if (key === 'var_95' || key === 'max_drawdown' || key === 'annualized_return' || key === 'win_rate') {
    return `${val >= 0 ? '+' : ''}${val.toFixed(2)}%`;
  }
  return val.toFixed ? val.toFixed(2) : String(val);
}

export default function Risk() {
  const [alerts, setAlerts] = useState([]);
  const [criticals, setCriticals] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [history, setHistory] = useState([]);
  const [regime, setRegime] = useState(null);
  const [showResolved, setShowResolved] = useState(false);
  const [runningMonitor, setRunningMonitor] = useState(false);
  const [runningMetrics, setRunningMetrics] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);

  const loadAlerts = useCallback(async () => {
    try {
      const [a, c] = await Promise.all([getAlerts(), getCriticalAlerts()]);
      setAlerts(Array.isArray(a) ? a : []);
      setCriticals(Array.isArray(c) ? c : []);
      setLastUpdated(new Date());
    } catch {}
  }, []);

  const loadMetrics = useCallback(async () => {
    try {
      const [m, h, r] = await Promise.all([getMetrics(), getMetricsHistory(), getRegime()]);
      setMetrics(m);
      setHistory(Array.isArray(h) ? h.slice(-30) : []);
      setRegime(r);
    } catch {}
  }, []);

  useEffect(() => {
    loadAlerts();
    loadMetrics();
    const t1 = setInterval(loadAlerts, 30000);
    const t2 = setInterval(loadMetrics, 300000);
    return () => { clearInterval(t1); clearInterval(t2); };
  }, [loadAlerts, loadMetrics]);

  const handleRunMonitor = async () => {
    setRunningMonitor(true);
    try { await runRiskMonitor(); await loadAlerts(); } catch {}
    setRunningMonitor(false);
  };

  const handleRunMetrics = async () => {
    setRunningMetrics(true);
    try { await runNightlyMetrics(); await loadMetrics(); } catch {}
    setRunningMetrics(false);
  };

  const handleResolve = async (alertId) => {
    try {
      await resolveAlert(alertId);
      setAlerts(prev => prev.map(a => a.id === alertId
        ? { ...a, resolved: true, resolved_at: new Date().toISOString() }
        : a
      ));
      setCriticals(prev => prev.filter(c => c.id !== alertId));
    } catch {}
  };

  const isRiskOff = regime && (regime.regime === 'Risk-Off' || regime.regime === 'Stagflation');
  const tiers = isRiskOff ? STOP_TIERS.riskOff : STOP_TIERS.normal;

  const sortOrder = { CRITICAL: 0, BREACH: 1, WARN: 2 };
  const visibleAlerts = alerts
    .filter(a => showResolved || !a.resolved)
    .sort((a, b) => (sortOrder[a.severity] ?? 3) - (sortOrder[b.severity] ?? 3));

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">

      {/* CRITICAL Banner */}
      {criticals.length > 0 && (
        <div className="rounded-xl bg-red-600 text-white p-4 shadow-lg">
          <div className="flex items-center gap-2 mb-2">
            <span className="inline-block w-3 h-3 rounded-full bg-white animate-pulse" />
            <span className="font-bold text-lg">CRITICAL ALERT ACTIVE — All new trade approvals are blocked</span>
          </div>
          {criticals.map((c, i) => (
            <p key={i} className="text-sm text-red-100 ml-5">{c.message}</p>
          ))}
        </div>
      )}

      {/* Alerts Section */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
          <div>
            <h2 className="text-base font-semibold text-gray-900">Active Alerts</h2>
            {lastUpdated && (
              <p className="text-xs text-gray-400 mt-0.5">
                Updated {Math.round((Date.now() - lastUpdated) / 1000)}s ago
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer">
              <input
                type="checkbox"
                checked={showResolved}
                onChange={e => setShowResolved(e.target.checked)}
                className="rounded"
              />
              Show Resolved
            </label>
            <button
              onClick={handleRunMonitor}
              disabled={runningMonitor}
              className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {runningMonitor ? 'Running…' : 'Run Risk Check'}
            </button>
            <button
              onClick={handleRunMetrics}
              disabled={runningMetrics}
              className="px-3 py-1.5 text-sm bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
            >
              {runningMetrics ? 'Running…' : 'Run Nightly Metrics'}
            </button>
          </div>
        </div>

        {visibleAlerts.length === 0 ? (
          <p className="text-sm text-gray-400 py-4 text-center">No active alerts — system is healthy</p>
        ) : (
          <div className="space-y-2">
            {visibleAlerts.map(a => (
              <RiskAlert key={a.id} alert={a} onResolve={handleResolve} />
            ))}
          </div>
        )}
      </div>

      {/* Performance Metrics */}
      <div>
        <h2 className="text-base font-semibold text-gray-900 mb-3">Performance Metrics</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {Object.entries(METRIC_META).map(([key, meta]) => {
            const val = metrics?.[key];
            return (
              <StatCard
                key={key}
                label={meta.label}
                plainLabel={meta.plain}
                value={fmtMetric(key, val)}
                tooltip={meta.tip}
                status={metricStatus(key, val)}
              />
            );
          })}
        </div>
      </div>

      {/* Stop-Loss Ladder */}
      <div className="bg-white rounded-xl border border-gray-200 p-5">
        <div className="flex items-center gap-3 mb-4">
          <h2 className="text-base font-semibold text-gray-900">Stop-Loss Protection Ladder</h2>
          {isRiskOff && (
            <span className="text-xs bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded-full font-medium">
              {regime.regime} Mode — Tighter Stops Active
            </span>
          )}
        </div>
        <div className="space-y-3">
          {[
            { tier: 'Tier 1 — Position Stop', pct: tiers.tier1, desc: 'Sell the individual position if it falls this much from your entry price.' },
            { tier: 'Tier 2 — Strategy Stop', pct: tiers.tier2, desc: 'Close all positions in this strategy if total strategy P&L falls this much.' },
            { tier: 'Tier 3 — Portfolio Stop', pct: tiers.tier3, desc: 'Halt all trading if the total portfolio falls this much.' },
          ].map(({ tier, pct, desc }) => (
            <div key={tier} className="flex items-start gap-4 p-3 rounded-lg bg-gray-50">
              <div className="flex-shrink-0 w-32">
                <div className="text-xs font-semibold text-gray-700">{tier}</div>
                <div className="text-2xl font-bold text-red-600 mt-0.5">{pct}%</div>
              </div>
              <div className="flex-1">
                <div className="text-sm text-gray-600">{desc}</div>
                <div className="mt-1.5 h-2 rounded-full bg-gray-200">
                  <div
                    className="h-full rounded-full bg-red-400"
                    style={{ width: `${Math.abs(pct)}%` }}
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Metrics History Chart */}
      {history.length > 1 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="text-base font-semibold text-gray-900 mb-4">Sharpe Ratio — History</h2>
          <ResponsiveContainer width="100%" height={140}>
            <BarChart data={history} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: '#9ca3af' }} axisLine={false} tickLine={false}
                tickFormatter={d => { try { return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }); } catch { return d; } }} />
              <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} axisLine={false} tickLine={false} />
              <RTooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <ReferenceLine y={1} stroke="#10b981" strokeDasharray="3 3" label={{ value: 'Good (1.0)', fontSize: 10, fill: '#10b981' }} />
              <Bar dataKey="sharpe_ratio" name="Sharpe Ratio" fill="#6366f1" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
