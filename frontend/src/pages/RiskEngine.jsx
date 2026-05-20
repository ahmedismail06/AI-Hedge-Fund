import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { getAlerts, getCriticalAlerts, resolveAlert, getMetrics, getMetricsHistory, runRiskMonitor, runNightlyMetrics } from '../api/risk';
import { getRegime } from '../api/macro';
import { getPositions } from '../api/portfolio';
import RiskAlert from '../components/RiskAlert';
import CorrelationMatrix from '../components/CorrelationMatrix';
import StopLadderGauge from '../components/StopLadderGauge';
import VarGauge from '../components/VarGauge';
import { BarChart, Bar, XAxis, YAxis, Tooltip as RTooltip, ResponsiveContainer, ReferenceLine, Cell } from 'recharts';

const METRIC_META = {
  sharpe_ratio:      { label: 'Sharpe',          fmt: v => v.toFixed(2),  good: v => v >= 1,   warn: v => v >= 0.5 },
  sortino_ratio:     { label: 'Sortino',          fmt: v => v.toFixed(2),  good: v => v >= 1,   warn: v => v >= 0.5 },
  var_95:            { label: 'VaR 95%',          fmt: v => `${v.toFixed(2)}%`, good: v => v > -5,  warn: v => v > -10 },
  max_drawdown:      { label: 'Max DD',           fmt: v => `${v.toFixed(2)}%`, good: v => v > -10, warn: v => v > -20 },
  calmar_ratio:      { label: 'Calmar',           fmt: v => v.toFixed(2),  good: v => v >= 1,   warn: v => v >= 0.5 },
  beta:              { label: 'Beta',             fmt: v => v.toFixed(2),  good: v => Math.abs(v) < 0.8, warn: v => Math.abs(v) < 1.2 },
  annualized_return: { label: 'Ann. Return',      fmt: v => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`, good: v => v >= 15, warn: v => v >= 0 },
  win_rate:          { label: 'Win Rate',         fmt: v => `${v.toFixed(1)}%`, good: v => v >= 55,  warn: v => v >= 45 },
};

function fmtMetric(key, val) {
  if (val == null) return '—';
  const m = METRIC_META[key];
  return m ? m.fmt(val) : val.toFixed ? val.toFixed(2) : String(val);
}

function metricColor(key, val) {
  if (val == null) return 'var(--text)';
  const m = METRIC_META[key];
  if (!m) return 'var(--text)';
  if (m.good(val)) return 'var(--green)';
  if (m.warn(val)) return 'var(--amber)';
  return 'var(--red)';
}

function SL({ children }) {
  return (
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
      {children}
    </div>
  );
}

function Panel({ children, style = {} }) {
  return (
    <div className="panel" style={style}>{children}</div>
  );
}

function PanelHeader({ label, title, action, onAction }) {
  return (
    <div className="panel-header">
      <div>
        <SL>{label}</SL>
        {title && <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text)', marginTop: '1px' }}>{title}</div>}
      </div>
      {action && (
        <button
          onClick={onAction}
          style={{ fontSize: '10px', fontWeight: 700, color: 'var(--accent)', fontFamily: 'Syne', background: 'none', border: 'none', cursor: 'pointer' }}
        >
          {action}
        </button>
      )}
    </div>
  );
}

export default function RiskEngine() {
  const { isAdmin } = useAuth();
  const [alerts, setAlerts]           = useState([]);
  const [criticals, setCriticals]     = useState([]);
  const [metrics, setMetrics]         = useState(null);
  const [history, setHistory]         = useState([]);
  const [regime, setRegime]           = useState(null);
  const [positions, setPositions]     = useState([]);
  const [showResolved, setShowResolved] = useState(false);
  const [runningMonitor, setRunningMonitor] = useState(false);
  const [runningMetrics, setRunningMetrics] = useState(false);

  const loadAlerts = useCallback(async () => {
    try {
      const [a, c] = await Promise.all([getAlerts(), getCriticalAlerts()]);
      setAlerts(Array.isArray(a) ? a : []);
      setCriticals(Array.isArray(c) ? c : []);
    } catch {}
  }, []);

  const loadData = useCallback(async () => {
    try {
      const [m, h, r, pos] = await Promise.all([getMetrics(), getMetricsHistory(), getRegime(), getPositions()]);
      setMetrics(m);
      setHistory(Array.isArray(h) ? h.slice(-30) : []);
      setRegime(r);
      setPositions(Array.isArray(pos) ? pos : []);
    } catch {}
  }, []);

  useEffect(() => {
    loadAlerts();
    loadData();
    const t1 = setInterval(loadAlerts, 30_000);
    const t2 = setInterval(loadData, 300_000);
    return () => { clearInterval(t1); clearInterval(t2); };
  }, [loadAlerts, loadData]);

  const handleRunMonitor = async () => {
    setRunningMonitor(true);
    try { await runRiskMonitor(); await loadAlerts(); } catch {}
    setRunningMonitor(false);
  };

  const handleRunMetrics = async () => {
    setRunningMetrics(true);
    try { await runNightlyMetrics(); await loadData(); } catch {}
    setRunningMetrics(false);
  };

  const handleResolve = async (alertId) => {
    try {
      await resolveAlert(alertId);
      setAlerts(prev => prev.map(a => a.id === alertId ? { ...a, resolved: true } : a));
      setCriticals(prev => prev.filter(c => c.id !== alertId));
    } catch {}
  };

  const isRiskOff = regime?.regime === 'Risk-Off' || regime?.regime === 'Stagflation';
  const sortOrder = { CRITICAL: 0, BREACH: 1, WARN: 2 };
  const visibleAlerts = alerts
    .filter(a => showResolved || !a.resolved)
    .sort((a, b) => (sortOrder[a.severity] ?? 3) - (sortOrder[b.severity] ?? 3));

  const portfolioValue = metrics?.portfolio_value ?? 0;
  const varValue = metrics?.var_95 ?? null;
  const currentDrawdown = metrics?.current_drawdown ?? null;

  const chartData = history.map(h => ({
    date: h.date ? new Date(h.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '',
    value: h.sharpe_ratio ?? null,
  })).filter(d => d.value != null);

  return (
    <div className="p-4 animate-fade-in" style={{ maxWidth: '1600px', margin: '0 auto' }}>

      {/* Critical banner */}
      {criticals.length > 0 && (
        <div
          className="rounded-xl px-5 py-3 flex items-center gap-3 mb-4"
          style={{ background: 'var(--red-bg)', border: '1px solid var(--red-border)' }}
        >
          <span className="w-2 h-2 rounded-full flex-shrink-0 pulse-critical" style={{ background: 'var(--red)' }} />
          <span className="font-bold text-[11px] tracking-widest uppercase flex-shrink-0" style={{ color: 'var(--red)', fontFamily: 'Syne' }}>
            Critical Alert
          </span>
          <span className="w-px h-4 flex-shrink-0" style={{ background: 'var(--red-border)' }} />
          <span className="text-[12px] truncate" style={{ color: 'var(--text)' }}>{criticals[0]?.message}</span>
          <span className="ml-auto text-[10px] font-bold flex-shrink-0" style={{ color: 'var(--red)', fontFamily: 'Syne' }}>
            {criticals.length} alert{criticals.length > 1 ? 's' : ''}
          </span>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr 300px', gap: '12px', alignItems: 'start' }}>

        {/* ── LEFT: Alerts + Stop Ladder ── */}
        <div className="space-y-3">
          <Panel>
            <PanelHeader
              label="Risk Alerts"
              title={`${visibleAlerts.length} active`}
            />
            <div className="p-3 space-y-1">
              <div className="flex items-center justify-between mb-2">
                <label style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '10px', color: 'var(--text-3)', fontFamily: 'Syne', cursor: 'pointer' }}>
                  <input type="checkbox" checked={showResolved} onChange={e => setShowResolved(e.target.checked)} />
                  Show resolved
                </label>
                <div className="flex gap-1">
                  <button
                    onClick={handleRunMonitor}
                    disabled={runningMonitor || !isAdmin}
                    style={{
                      fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
                      padding: '3px 8px', borderRadius: '5px',
                      background: 'var(--accent)', color: '#000',
                      border: 'none', cursor: runningMonitor || !isAdmin ? 'not-allowed' : 'pointer',
                      opacity: runningMonitor || !isAdmin ? 0.5 : 1,
                    }}
                  >
                    {runningMonitor ? '…' : 'Run Check'}
                  </button>
                  <button
                    onClick={handleRunMetrics}
                    disabled={runningMetrics || !isAdmin}
                    style={{
                      fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
                      padding: '3px 8px', borderRadius: '5px',
                      background: 'var(--surface-2)', color: 'var(--text-2)',
                      border: '1px solid var(--border)', cursor: runningMetrics || !isAdmin ? 'not-allowed' : 'pointer',
                      opacity: runningMetrics || !isAdmin ? 0.5 : 1,
                    }}
                  >
                    {runningMetrics ? '…' : 'Run Metrics'}
                  </button>
                </div>
              </div>

              {visibleAlerts.length === 0 ? (
                <div className="py-6 text-center" style={{ fontSize: '12px', color: 'var(--text-3)' }}>
                  <span className="material-symbols-outlined" style={{ fontSize: '20px', color: 'var(--green)', display: 'block', marginBottom: '4px' }}>check_circle</span>
                  System healthy
                </div>
              ) : (
                <div className="space-y-1 term-scroll" style={{ maxHeight: '300px', overflowY: 'auto' }}>
                  {visibleAlerts.map(a => (
                    <RiskAlert key={a.id} alert={a} onResolve={isAdmin ? handleResolve : undefined} />
                  ))}
                </div>
              )}
            </div>
          </Panel>

          {/* Stop Ladder */}
          <Panel>
            <PanelHeader label="Stop-Loss Ladder" title={isRiskOff ? `${regime.regime} — Tighter` : 'Normal Mode'} />
            <div className="p-3">
              <StopLadderGauge isRiskOff={isRiskOff} currentDrawdown={currentDrawdown} />
            </div>
          </Panel>
        </div>

        {/* ── CENTER: Metrics Grid + Sharpe History ── */}
        <div className="space-y-3">
          {/* 2×4 metric cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px' }}>
            {Object.entries(METRIC_META).map(([key, meta]) => {
              const val = metrics?.[key];
              return (
                <div
                  key={key}
                  className="rounded-xl p-3"
                  style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
                >
                  <SL>{meta.label}</SL>
                  <div
                    style={{
                      fontFamily: 'JetBrains Mono',
                      fontWeight: 700,
                      fontSize: '22px',
                      lineHeight: 1,
                      marginTop: '6px',
                      color: metricColor(key, val),
                    }}
                  >
                    {fmtMetric(key, val)}
                  </div>
                </div>
              );
            })}
          </div>

          {/* Sharpe history chart */}
          <Panel>
            <PanelHeader label="History" title="Rolling Sharpe Ratio — Last 30 Sessions" />
            <div className="px-4 py-3">
              {chartData.length < 2 ? (
                <div className="py-8 text-center" style={{ fontSize: '12px', color: 'var(--text-3)' }}>
                  Insufficient history — run nightly metrics to populate
                </div>
              ) : (
                <ResponsiveContainer width="100%" height={160}>
                  <BarChart data={chartData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
                      axisLine={false}
                      tickLine={false}
                      interval="preserveStartEnd"
                    />
                    <YAxis
                      tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
                      axisLine={false}
                      tickLine={false}
                      width={28}
                    />
                    <RTooltip
                      contentStyle={{ fontSize: 11, borderRadius: 8, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }}
                      formatter={v => [v?.toFixed(2), 'Sharpe']}
                    />
                    <ReferenceLine y={1} stroke="var(--green)" strokeDasharray="3 3" />
                    <ReferenceLine y={0} stroke="var(--border)" />
                    <Bar dataKey="value" radius={[3, 3, 0, 0]}>
                      {chartData.map((d, i) => (
                        <Cell key={i} fill={d.value >= 1 ? 'var(--green)' : d.value >= 0.5 ? 'var(--amber)' : d.value >= 0 ? 'var(--accent)' : 'var(--red)'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              )}
            </div>
          </Panel>
        </div>

        {/* ── RIGHT: Correlation + VaR Gauge ── */}
        <div className="space-y-3">
          <Panel>
            <PanelHeader label="Position Correlation" title="Diversification map" />
            <div className="p-3">
              <CorrelationMatrix positions={positions} />
            </div>
          </Panel>

          <Panel>
            <PanelHeader label="Value at Risk" title="95% 1-Day VaR" />
            <div className="p-4 flex flex-col items-center">
              <VarGauge varValue={varValue} portfolioValue={portfolioValue} />
              <div className="mt-3 text-center">
                <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'Syne', letterSpacing: '0.1em', textTransform: 'uppercase' }}>
                  Historical Simulation · 95% Confidence
                </div>
                <div style={{ fontSize: '10px', color: 'var(--text-3)', marginTop: '3px' }}>
                  No normality assumption
                </div>
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
