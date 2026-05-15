import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { getAlerts, getCriticalAlerts, resolveAlert, getMetrics, getMetricsHistory, runRiskMonitor, runNightlyMetrics } from '../api/risk';
import { getRegime } from '../api/macro';
import RiskAlert from '../components/RiskAlert';
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

const STATUS_COLOR = { ok: 'var(--green)', warn: 'var(--amber)', critical: 'var(--red)', neutral: 'var(--text)' };

function SL({ children }) {
  return (
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
      {children}
    </div>
  );
}

function Card({ children, className = '', style = {} }) {
  return (
    <div className={`rounded-xl p-5 ${className}`} style={{ background: 'var(--surface)', border: '1px solid var(--border)', ...style }}>
      {children}
    </div>
  );
}

function MetricCard({ label, plainLabel, value, status }) {
  return (
    <div className="rounded-xl p-4" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
      <SL>{label}</SL>
      {plainLabel && (
        <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-3)', fontFamily: 'Syne' }}>
          {plainLabel}
        </div>
      )}
      <div
        className="text-[22px] font-bold mt-1 leading-none"
        style={{ color: STATUS_COLOR[status] ?? 'var(--text)', fontFamily: 'JetBrains Mono' }}
      >
        {value}
      </div>
    </div>
  );
}

export default function Risk() {
  const { isAdmin } = useAuth();
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

  const criticalCount = criticals.length;
  const warnCount = visibleAlerts.filter(a => a.severity === 'WARN').length;

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">

      {/* CRITICAL Banner */}
      {criticals.length > 0 && (
        <div className="rounded-xl p-4" style={{ background: 'var(--red-bg)', border: '1px solid var(--red-border)' }}>
          <div className="flex items-center gap-2 mb-2">
            <span
              style={{
                display: 'inline-block', width: '10px', height: '10px',
                borderRadius: '50%', background: 'var(--red)',
                animation: 'pulse 1.5s ease-in-out infinite',
              }}
            />
            <span style={{ fontFamily: 'Syne', fontWeight: 700, fontSize: '15px', color: 'var(--red)' }}>
              CRITICAL ALERT ACTIVE — All new trade approvals are blocked
            </span>
          </div>
          {criticals.map((c, i) => (
            <p key={i} className="ml-5 text-sm" style={{ color: 'var(--red)', opacity: 0.85 }}>{c.message}</p>
          ))}
        </div>
      )}

      {/* Alerts Section */}
      <Card>
        <div className="flex items-center justify-between mb-4 flex-wrap gap-3">
          <div>
            <SL>Risk Alerts</SL>
            <div className="flex items-center gap-2 mt-1">
              <span style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text)', fontFamily: 'Syne' }}>
                Active Alerts
              </span>
              {criticalCount > 0 && (
                <span style={{
                  fontSize: '11px', fontWeight: 600, fontFamily: 'Syne',
                  background: 'var(--red-bg)', color: 'var(--red)',
                  border: '1px solid var(--red-border)',
                  padding: '1px 8px', borderRadius: '999px',
                }}>
                  {criticalCount} Critical
                </span>
              )}
              {warnCount > 0 && (
                <span style={{
                  fontSize: '11px', fontWeight: 600, fontFamily: 'Syne',
                  background: 'var(--amber-bg)', color: 'var(--amber)',
                  border: '1px solid var(--amber-border)',
                  padding: '1px 8px', borderRadius: '999px',
                }}>
                  {warnCount} Warn
                </span>
              )}
            </div>
            {lastUpdated && (
              <p style={{ fontSize: '11px', color: 'var(--text-3)', marginTop: '2px', fontFamily: 'Syne' }}>
                Updated {Math.round((Date.now() - lastUpdated) / 1000)}s ago
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', color: 'var(--text-2)', cursor: 'pointer', fontFamily: 'Syne' }}>
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
              disabled={runningMonitor || !isAdmin}
              title={!isAdmin ? 'Guest mode — read only' : undefined}
              style={{
                padding: '6px 12px',
                fontSize: '13px',
                fontWeight: 600,
                fontFamily: 'Syne',
                background: 'var(--accent)',
                color: '#000',
                border: 'none',
                borderRadius: '8px',
                cursor: runningMonitor || !isAdmin ? 'not-allowed' : 'pointer',
                opacity: runningMonitor || !isAdmin ? 0.5 : 1,
                transition: 'opacity 0.15s',
              }}
            >
              {runningMonitor ? 'Running…' : 'Run Risk Check'}
            </button>
            <button
              onClick={handleRunMetrics}
              disabled={runningMetrics || !isAdmin}
              title={!isAdmin ? 'Guest mode — read only' : undefined}
              style={{
                padding: '6px 12px',
                fontSize: '13px',
                fontWeight: 600,
                fontFamily: 'Syne',
                background: 'var(--surface-2)',
                color: 'var(--text-2)',
                border: '1px solid var(--border)',
                borderRadius: '8px',
                cursor: runningMetrics || !isAdmin ? 'not-allowed' : 'pointer',
                opacity: runningMetrics || !isAdmin ? 0.5 : 1,
                transition: 'opacity 0.15s',
              }}
            >
              {runningMetrics ? 'Running…' : 'Run Nightly Metrics'}
            </button>
          </div>
        </div>

        {visibleAlerts.length === 0 ? (
          <p className="text-sm py-4 text-center" style={{ color: 'var(--text-3)' }}>No active alerts — system is healthy</p>
        ) : (
          <div className="space-y-2">
            {visibleAlerts.map(a => (
              <RiskAlert key={a.id} alert={a} onResolve={isAdmin ? handleResolve : undefined} />
            ))}
          </div>
        )}
      </Card>

      {/* Performance Metrics */}
      <div>
        <div className="mb-3">
          <SL>Performance</SL>
          <div style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text)', fontFamily: 'Syne', marginTop: '4px' }}>
            Performance Metrics
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {Object.entries(METRIC_META).map(([key, meta]) => {
            const val = metrics?.[key];
            return (
              <MetricCard
                key={key}
                label={meta.label}
                plainLabel={meta.plain}
                value={fmtMetric(key, val)}
                status={metricStatus(key, val)}
              />
            );
          })}
        </div>
      </div>

      {/* Stop-Loss Ladder */}
      <Card>
        <div className="flex items-center gap-3 mb-4">
          <div>
            <SL>Risk Controls</SL>
            <div style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text)', fontFamily: 'Syne', marginTop: '4px' }}>
              Stop-Loss Protection Ladder
            </div>
          </div>
          {isRiskOff && (
            <span style={{
              fontSize: '11px', fontWeight: 600, fontFamily: 'Syne',
              background: 'var(--amber-bg)', color: 'var(--amber)',
              border: '1px solid var(--amber-border)',
              padding: '2px 10px', borderRadius: '999px',
            }}>
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
            <div
              key={tier}
              className="flex items-start gap-4 p-3 rounded-lg"
              style={{ background: 'var(--surface-2)' }}
            >
              <div style={{ flexShrink: 0, width: '128px' }}>
                <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-2)', fontFamily: 'Syne' }}>
                  {tier}
                </div>
                <div style={{ fontFamily: 'JetBrains Mono', fontWeight: 700, fontSize: '22px', color: 'var(--red)', marginTop: '2px', lineHeight: 1 }}>
                  {pct}%
                </div>
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: '13px', color: 'var(--text-2)' }}>{desc}</div>
                <div className="mt-1.5 h-2 rounded-full" style={{ background: 'var(--border)' }}>
                  <div
                    className="h-full rounded-full"
                    style={{ width: `${Math.abs(pct)}%`, background: 'var(--red)' }}
                  />
                </div>
              </div>
            </div>
          ))}
        </div>
      </Card>

      {/* Metrics History Chart */}
      {history.length > 1 && (
        <Card>
          <div className="mb-4">
            <SL>History</SL>
            <div style={{ fontSize: '15px', fontWeight: 700, color: 'var(--text)', fontFamily: 'Syne', marginTop: '4px' }}>
              Sharpe Ratio — History
            </div>
          </div>
          <ResponsiveContainer width="100%" height={140}>
            <BarChart data={history} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <XAxis
                dataKey="date"
                tick={{ fontSize: 10, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
                axisLine={false}
                tickLine={false}
                tickFormatter={d => { try { return new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }); } catch { return d; } }}
              />
              <YAxis
                tick={{ fontSize: 10, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
                axisLine={false}
                tickLine={false}
              />
              <RTooltip
                contentStyle={{ fontSize: 12, borderRadius: 8, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }}
              />
              <ReferenceLine
                y={1}
                stroke="var(--green)"
                strokeDasharray="3 3"
                label={{ value: 'Good (1.0)', fontSize: 10, fill: 'var(--green)' }}
              />
              <Bar dataKey="sharpe_ratio" name="Sharpe Ratio" fill="var(--accent)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      )}
    </div>
  );
}
