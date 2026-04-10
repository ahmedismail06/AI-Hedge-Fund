import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { getPositions, getPending, getExposure, approveTrade, rejectTrade } from '../api/portfolio';
import { getAlerts, getCriticalAlerts, getMetrics } from '../api/risk';
import { getRegime, getBriefing } from '../api/macro';
import { getExecutionStatus } from '../api/execution';
import { getHistory } from '../api/research';
import EquityCurveChart from '../components/EquityCurveChart';
import RiskAlert from '../components/RiskAlert';
import ConfirmDialog from '../components/ConfirmDialog';
import ConvictionBadge from '../components/ConvictionBadge';

const REGIME_STYLES = {
  'Risk-On':     { bg: 'bg-green-100',  border: 'border-green-300', text: 'text-green-700', dot: 'bg-green-500' },
  'Risk-Off':    { bg: 'bg-red-100',    border: 'border-red-300',   text: 'text-red-700',   dot: 'bg-red-500' },
  'Transitional':{ bg: 'bg-yellow-100', border: 'border-yellow-300',text: 'text-yellow-700',dot: 'bg-yellow-500' },
  'Stagflation': { bg: 'bg-orange-100', border: 'border-orange-300',text: 'text-orange-700',dot: 'bg-orange-500' },
};

const SUB_SCORE_LABELS = {
  growth_score:    'Growth',
  inflation_score: 'Inflation',
  fed_score:       'Fed',
  stress_score:    'Stress',
};

const fmt$ = (v) =>
  v == null ? '—'
  : Math.abs(v) >= 1000000
  ? `$${(v / 1000000).toFixed(2)}M`
  : Math.abs(v) >= 1000
  ? `$${(v / 1000).toFixed(1)}k`
  : `$${v.toFixed(2)}`;

function fmtAgo(ts) {
  if (!ts) return null;
  const diff = Math.round((Date.now() - new Date(ts)) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  return `${Math.round(diff / 3600)}h ago`;
}

function HealthDot({ ok, label, sub, badge, badgeColor, onClick }) {
  return (
    <div
      className={`flex items-center gap-2 bg-white rounded-lg border border-gray-200 px-3 py-2.5 ${onClick ? 'cursor-pointer hover:bg-gray-50' : ''}`}
      onClick={onClick}
    >
      <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${ok ? 'bg-green-400' : ok === false ? 'bg-red-400' : 'bg-gray-300'}`} />
      <div className="min-w-0">
        <div className="text-xs font-medium text-gray-700 truncate">{label}</div>
        {sub && <div className="text-xs text-gray-400 truncate">{sub}</div>}
      </div>
      {badge != null && badge > 0 && (
        <span className={`ml-auto text-xs font-bold px-1.5 py-0.5 rounded-full ${badgeColor ?? 'bg-blue-100 text-blue-700'}`}>
          {badge}
        </span>
      )}
    </div>
  );
}

function SubScorePill({ label, value }) {
  const isPos = (value ?? 0) >= 0;
  return (
    <div className="text-center">
      <div className="text-xs text-gray-500 mb-1">{label}</div>
      <div className={`text-sm font-bold ${isPos ? 'text-green-600' : 'text-red-600'}`}>
        {value != null ? `${isPos ? '+' : ''}${value.toFixed(1)}` : '—'}
      </div>
      <div className="mt-1 h-1.5 rounded-full bg-gray-100 w-16 mx-auto">
        <div
          className={`h-full rounded-full ${isPos ? 'bg-green-400' : 'bg-red-400'}`}
          style={{ width: `${Math.abs(value ?? 0) * 100}%` }}
        />
      </div>
    </div>
  );
}

const VERDICT_COLORS = {
  LONG:  'bg-green-100 text-green-700',
  SHORT: 'bg-red-100 text-red-700',
  AVOID: 'bg-gray-100 text-gray-600',
};

export default function Dashboard() {
  const navigate = useNavigate();
  const [positions, setPositions] = useState([]);
  const [pending, setPending] = useState([]);
  const [exposure, setExposure] = useState(null);
  const [alerts, setAlerts] = useState([]);
  const [criticals, setCriticals] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [regime, setRegime] = useState(null);
  const [briefing, setBriefing] = useState(null);
  const [execStatus, setExecStatus] = useState(null);
  const [memoHistory, setMemoHistory] = useState([]);
  const [confirm, setConfirm] = useState(null);

  const loadPnL = useCallback(async () => {
    try {
      const [pos, pend, exp] = await Promise.all([getPositions(), getPending(), getExposure()]);
      setPositions(Array.isArray(pos) ? pos : []);
      setPending(Array.isArray(pend) ? pend : []);
      setExposure(exp);
    } catch {}
  }, []);

  const loadAlerts = useCallback(async () => {
    try {
      const [a, c] = await Promise.all([getAlerts(), getCriticalAlerts()]);
      setAlerts(Array.isArray(a) ? a.filter(x => !x.resolved).slice(0, 3) : []);
      setCriticals(Array.isArray(c) ? c : []);
    } catch {}
  }, []);

  const loadHealth = useCallback(async () => {
    try {
      const [met, reg, brief, exec] = await Promise.all([getMetrics(), getRegime(), getBriefing(), getExecutionStatus()]);
      setMetrics(met);
      setRegime(reg);
      setBriefing(brief);
      setExecStatus(exec);
    } catch {}
  }, []);

  const loadMemos = useCallback(async () => {
    try {
      const data = await getHistory();
      setMemoHistory(Array.isArray(data) ? data.slice(0, 3) : []);
    } catch {}
  }, []);

  useEffect(() => {
    loadPnL(); loadAlerts(); loadHealth(); loadMemos();
    const t1 = setInterval(loadPnL, 60000);
    const t2 = setInterval(loadAlerts, 30000);
    const t3 = setInterval(loadHealth, 300000);
    return () => { clearInterval(t1); clearInterval(t2); clearInterval(t3); };
  }, [loadPnL, loadAlerts, loadHealth, loadMemos]);

  const maxDrawdown = metrics?.max_drawdown;
  const regimeKey = regime?.regime ?? briefing?.regime;
  const regStyle = REGIME_STYLES[regimeKey] || REGIME_STYLES['Transitional'];
  const ibkrOk = execStatus?.ibkr_connected === true;
  const isPaper = execStatus?.is_paper === true;

  // IBKR account values (live from broker when connected, null when disconnected)
  const portfolioValue = execStatus?.net_liquidation ?? null;
  const cashBalance = execStatus?.cash ?? null;
  const unrealizedPnl = execStatus?.unrealized_pnl ?? null;
  const realizedPnl = execStatus?.realized_pnl ?? null;

  const handleConfirm = async () => {
    if (!confirm) return;
    const { action, id } = confirm;
    setPending(prev => prev.filter(p => p.id !== id));
    setConfirm(null);
    try {
      if (action === 'approve') await approveTrade(id);
      else await rejectTrade(id);
    } catch { loadPnL(); }
  };

  // Build equity curve data from positions history (basic: just total unrealized over time)
  // If no history endpoint returns chart data, show empty
  const equityData = [];

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      {confirm && (
        <ConfirmDialog
          title={confirm.action === 'approve' ? `Approve trade for ${confirm.ticker}?` : `Reject trade for ${confirm.ticker}?`}
          message={confirm.action === 'approve'
            ? 'This will send the order to the execution engine.'
            : 'This will reject the sizing recommendation.'}
          confirmLabel={confirm.action === 'approve' ? 'Yes, Approve' : 'Yes, Reject'}
          destructive={confirm.action === 'reject'}
          onConfirm={handleConfirm}
          onCancel={() => setConfirm(null)}
        />
      )}

      {/* CRITICAL Banner */}
      {criticals.length > 0 && (
        <div
          className="rounded-xl bg-red-600 text-white px-5 py-3 flex items-center gap-3 cursor-pointer hover:bg-red-700 transition-colors"
          onClick={() => navigate('/risk')}
        >
          <span className="w-3 h-3 rounded-full bg-white animate-pulse flex-shrink-0" />
          <span className="font-bold">CRITICAL ALERT — Trade approvals blocked.</span>
          <span className="text-sm text-red-100 ml-1">{criticals[0]?.message}</span>
          <span className="ml-auto text-sm underline">View Risk →</span>
        </div>
      )}

      {/* P&L Strip — sourced from IBKR when connected */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <div className="text-xs font-medium text-gray-500 uppercase tracking-wide">Portfolio Value</div>
          <div className="text-2xl font-bold mt-1 text-gray-900">{fmt$(portfolioValue)}</div>
          <div className="text-xs text-gray-400 mt-0.5">
            {ibkrOk ? (isPaper ? 'Paper account · live from IBKR' : 'Live account · from IBKR') : 'IBKR disconnected'}
          </div>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <div className="text-xs font-medium text-gray-500 uppercase tracking-wide">Cash Available</div>
          <div className="text-2xl font-bold mt-1 text-gray-900">{fmt$(cashBalance)}</div>
          <div className="text-xs text-gray-400 mt-0.5">Buying power</div>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <div className="text-xs font-medium text-gray-500 uppercase tracking-wide">Unrealized P&L</div>
          <div className={`text-2xl font-bold mt-1 ${unrealizedPnl == null ? 'text-gray-900' : unrealizedPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
            {fmt$(unrealizedPnl)}
          </div>
          <div className="text-xs text-gray-400 mt-0.5">Open positions</div>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4">
          <div className="text-xs font-medium text-gray-500 uppercase tracking-wide">Realized P&L</div>
          <div className={`text-2xl font-bold mt-1 ${realizedPnl == null ? 'text-gray-900' : realizedPnl >= 0 ? 'text-green-600' : 'text-red-600'}`}>
            {fmt$(realizedPnl)}
          </div>
          <div className="text-xs text-gray-400 mt-0.5">Session closed trades</div>
        </div>
      </div>

      {/* System Health Strip */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
        <HealthDot
          ok={ibkrOk}
          label="IBKR Gateway"
          sub={ibkrOk ? (isPaper ? 'Paper mode' : 'Live mode') : 'Disconnected'}
          onClick={() => navigate('/execution')}
        />
        <HealthDot
          ok={briefing?.created_at ? true : null}
          label="Macro Engine"
          sub={briefing?.created_at ? fmtAgo(briefing.created_at) : 'No data'}
          onClick={() => navigate('/macro')}
        />
        <HealthDot
          ok={true}
          label="Screener"
          sub="Ready"
          onClick={() => navigate('/screener')}
        />
        <HealthDot
          ok={alerts.length === 0 && criticals.length === 0}
          label="Risk Monitor"
          sub={criticals.length > 0 ? `${criticals.length} critical` : alerts.length > 0 ? `${alerts.length} alerts` : 'All clear'}
          badge={criticals.length || undefined}
          badgeColor="bg-red-100 text-red-700"
          onClick={() => navigate('/risk')}
        />
        <HealthDot
          ok={pending.length === 0}
          label="Pending Approvals"
          sub={pending.length > 0 ? 'Awaiting your review' : 'None'}
          badge={pending.length || undefined}
          badgeColor="bg-yellow-100 text-yellow-700"
          onClick={() => navigate('/portfolio')}
        />
        <HealthDot
          ok={regimeKey != null}
          label="Regime"
          sub={regimeKey ?? 'Unknown'}
          onClick={() => navigate('/macro')}
        />
      </div>

      {/* Regime Card */}
      <div className={`rounded-xl border p-5 ${regStyle.bg} ${regStyle.border}`}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className={`w-4 h-4 rounded-full ${regStyle.dot}`} />
            <div>
              <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">Market Regime</p>
              <p className={`text-2xl font-black ${regStyle.text}`}>{regimeKey ?? '—'}</p>
            </div>
            {regime?.regime_confidence != null && (
              <div className="ml-4 text-sm text-gray-600">
                Confidence <strong>{regime.regime_confidence}/10</strong>
              </div>
            )}
          </div>
          <div className="flex gap-6">
            {Object.entries(SUB_SCORE_LABELS).map(([key, label]) => (
              <SubScorePill key={key} label={label} value={regime?.[key] ?? briefing?.[key]} />
            ))}
          </div>
          <button
            onClick={() => navigate('/macro')}
            className={`text-xs underline ${regStyle.text}`}
          >
            Full Macro View →
          </button>
        </div>
      </div>

      {/* Equity Curve + Pending Approvals */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
        <div className="lg:col-span-3 bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-700">Portfolio Equity Curve</h2>
            <span className="text-xs text-gray-400">Based on closed fill history</span>
          </div>
          <EquityCurveChart data={equityData} height={180} />
        </div>

        <div className="lg:col-span-2 bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-700">
              Awaiting Your Approval
              {pending.length > 0 && (
                <span className="ml-2 bg-yellow-100 text-yellow-700 text-xs px-1.5 py-0.5 rounded-full">{pending.length}</span>
              )}
            </h2>
            {pending.length > 3 && (
              <button onClick={() => navigate('/portfolio')} className="text-xs text-blue-500 hover:underline">
                See All →
              </button>
            )}
          </div>
          {pending.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-gray-400">
              <span className="text-2xl mb-1">✓</span>
              <p className="text-sm">No pending approvals</p>
            </div>
          ) : (
            <div className="space-y-3">
              {pending.slice(0, 3).map(item => (
                <div key={item.id} className="border border-gray-100 rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="font-mono font-bold text-gray-900">{item.ticker}</span>
                    <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${VERDICT_COLORS[item.verdict] || 'bg-gray-100 text-gray-600'}`}>
                      {item.verdict}
                    </span>
                    <ConvictionBadge score={item.conviction_score} />
                    {item.size_label && (
                      <span className="text-xs text-gray-500 ml-auto">{item.size_label}</span>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setConfirm({ action: 'approve', id: item.id, ticker: item.ticker })}
                      className="flex-1 py-1.5 text-xs font-medium bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => setConfirm({ action: 'reject', id: item.id, ticker: item.ticker })}
                      className="flex-1 py-1.5 text-xs font-medium border border-red-200 text-red-600 rounded-lg hover:bg-red-50 transition-colors"
                    >
                      Reject
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Alerts + Recent Memos */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-700">Recent Risk Alerts</h2>
            <button onClick={() => navigate('/risk')} className="text-xs text-blue-500 hover:underline">See All →</button>
          </div>
          {alerts.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-24 text-gray-400">
              <span className="text-xl mb-1">✓</span>
              <p className="text-sm">No active alerts</p>
            </div>
          ) : (
            <div className="space-y-2">
              {alerts.map(a => <RiskAlert key={a.id} alert={a} compact />)}
            </div>
          )}
        </div>

        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-gray-700">Recent Research Memos</h2>
            <button onClick={() => navigate('/research')} className="text-xs text-blue-500 hover:underline">See All →</button>
          </div>
          {memoHistory.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-24 text-gray-400">
              <p className="text-sm">No research memos yet</p>
            </div>
          ) : (
            <div className="space-y-2">
              {memoHistory.map((memo, i) => (
                <div
                  key={memo.id ?? i}
                  className="flex items-center gap-3 p-2.5 rounded-lg hover:bg-gray-50 cursor-pointer transition-colors"
                  onClick={() => navigate('/research')}
                >
                  <span className="font-mono font-bold text-gray-900 w-16">{memo.ticker}</span>
                  <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${VERDICT_COLORS[memo.verdict] || 'bg-gray-100 text-gray-600'}`}>
                    {memo.verdict}
                  </span>
                  <ConvictionBadge score={memo.conviction_score} />
                  <span className="text-xs text-gray-400 ml-auto">
                    {memo.date ? new Date(memo.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : ''}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
