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

/* ─── Regime config (CSS variables for both themes) ──────────────── */
const REGIME_CONFIG = {
  'Risk-On': {
    cls: 'regime-risk-on', dotCls: 'dot-green',
    colorVar: 'var(--regime-on-text)', label: 'RISK-ON',
  },
  'Risk-Off': {
    cls: 'regime-risk-off', dotCls: 'dot-red',
    colorVar: 'var(--regime-off-text)', label: 'RISK-OFF',
  },
  'Stagflation': {
    cls: 'regime-stagflation', dotCls: 'dot-amber',
    colorVar: 'var(--regime-st-text)', label: 'STAGFLATION',
  },
  'Transitional': {
    cls: 'regime-transitional', dotCls: 'dot-blue',
    colorVar: 'var(--regime-tr-text)', label: 'TRANSITIONAL',
  },
};

const SUB_SCORES = [
  { key: 'growth_score',    label: 'Growth' },
  { key: 'inflation_score', label: 'Inflation' },
  { key: 'fed_score',       label: 'Fed' },
  { key: 'stress_score',    label: 'Stress' },
];

const VERDICT_STYLES = {
  LONG:  { bgVar: 'var(--green-bg)',  colorVar: 'var(--green)' },
  SHORT: { bgVar: 'var(--red-bg)',    colorVar: 'var(--red)' },
  AVOID: { bgVar: 'var(--surface-2)', colorVar: 'var(--text-2)' },
};

/* ─── Formatters ─────────────────────────────────────────────────── */
function fmt$(v) {
  if (v == null) return '—';
  const abs    = Math.abs(v);
  const prefix = v < 0 ? '-' : '';
  if (abs >= 1_000_000) return `${prefix}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000)     return `${prefix}$${(abs / 1_000).toFixed(1)}k`;
  return `${prefix}$${abs.toFixed(2)}`;
}

function fmtAgo(ts) {
  if (!ts) return null;
  const diff = Math.round((Date.now() - new Date(ts)) / 1000);
  if (diff < 60)   return `${diff}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  return `${Math.round(diff / 3600)}h ago`;
}

/* ─── PnL Card ───────────────────────────────────────────────────── */
function PnLCard({ label, value, sub, colorVar }) {
  return (
    <div
      className="rounded-lg p-4 card-hover"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      <div className="section-label mb-2">{label}</div>
      <div
        className="text-2xl font-semibold font-data leading-none"
        style={{ color: colorVar ?? 'var(--text)', fontFamily: 'JetBrains Mono' }}
      >
        {value ?? '—'}
      </div>
      <div className="text-[10px] mt-1.5 font-data" style={{ color: 'var(--text-3)' }}>{sub}</div>
    </div>
  );
}

/* ─── Health Pill ────────────────────────────────────────────────── */
function HealthPill({ ok, label, sub, badge, badgeStyle, onClick }) {
  const dotCls = ok === true ? 'dot-green' : ok === false ? 'dot-red' : 'dot-gray';

  return (
    <button
      onClick={onClick}
      className="flex items-center gap-2.5 rounded-md px-3 py-2.5 text-left w-full transition-all card-hover"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)', cursor: onClick ? 'pointer' : 'default' }}
    >
      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${dotCls}`} />
      <div className="min-w-0 flex-1">
        <div className="text-[11px] font-bold truncate" style={{ color: 'var(--text)', fontFamily: 'Syne' }}>
          {label}
        </div>
        {sub && (
          <div className="text-[10px] truncate font-data" style={{ color: 'var(--text-3)' }}>
            {sub}
          </div>
        )}
      </div>
      {badge != null && badge > 0 && (
        <span
          className="text-[9px] font-bold px-1.5 py-0.5 rounded-sm font-data flex-shrink-0"
          style={badgeStyle}
        >
          {badge}
        </span>
      )}
    </button>
  );
}

/* ─── Sub-score bar ──────────────────────────────────────────────── */
function SubScore({ label, value }) {
  const v   = value ?? 0;
  const pos = v >= 0;
  const pct = Math.min(Math.abs(v) * 100, 100);

  return (
    <div className="text-center">
      <div className="section-label mb-1.5">{label}</div>
      <div
        className="text-sm font-semibold font-data"
        style={{ color: pos ? 'var(--green)' : 'var(--red)', fontFamily: 'JetBrains Mono' }}
      >
        {value != null ? `${pos ? '+' : ''}${v.toFixed(2)}` : '—'}
      </div>
      <div
        className="mt-1.5 h-1 rounded-full mx-auto"
        style={{ width: '48px', background: 'var(--border)' }}
      >
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, background: pos ? 'var(--green)' : 'var(--red)', opacity: 0.8 }}
        />
      </div>
    </div>
  );
}

/* ─── Main component ─────────────────────────────────────────────── */
export default function Dashboard() {
  const navigate = useNavigate();
  const [positions,   setPositions]   = useState([]);
  const [pending,     setPending]     = useState([]);
  const [alerts,      setAlerts]      = useState([]);
  const [criticals,   setCriticals]   = useState([]);
  const [metrics,     setMetrics]     = useState(null);
  const [regime,      setRegime]      = useState(null);
  const [briefing,    setBriefing]    = useState(null);
  const [execStatus,  setExecStatus]  = useState(null);
  const [memoHistory, setMemoHistory] = useState([]);
  const [confirm,     setConfirm]     = useState(null);

  const loadPnL = useCallback(async () => {
    try {
      const [pos, pend] = await Promise.all([getPositions(), getPending()]);
      setPositions(Array.isArray(pos) ? pos : []);
      setPending(Array.isArray(pend) ? pend : []);
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
      const [, reg, brief, exec] = await Promise.all([
        getMetrics(), getRegime(), getBriefing(), getExecutionStatus(),
      ]);
      setRegime(reg);
      setBriefing(brief);
      setExecStatus(exec);
    } catch {}
  }, []);

  const loadMemos = useCallback(async () => {
    try {
      const data = await getHistory();
      setMemoHistory(Array.isArray(data) ? data.slice(0, 5) : []);
    } catch {}
  }, []);

  useEffect(() => {
    loadPnL(); loadAlerts(); loadHealth(); loadMemos();
    const t1 = setInterval(loadPnL,    60_000);
    const t2 = setInterval(loadAlerts, 30_000);
    const t3 = setInterval(loadHealth, 300_000);
    return () => { clearInterval(t1); clearInterval(t2); clearInterval(t3); };
  }, [loadPnL, loadAlerts, loadHealth, loadMemos]);

  const regimeKey = regime?.regime ?? briefing?.regime;
  const regCfg    = REGIME_CONFIG[regimeKey] ?? null;
  const ibkrOk    = execStatus?.ibkr_connected === true;
  const isPaper   = execStatus?.is_paper === true;

  const portfolioValue = execStatus?.net_liquidation ?? null;
  const cashBalance    = execStatus?.cash             ?? null;
  const unrealizedPnl  = execStatus?.unrealized_pnl   ?? null;
  const realizedPnl    = execStatus?.realized_pnl     ?? null;

  const pnlColor = (v) => v == null ? undefined : v >= 0 ? 'var(--green)' : 'var(--red)';

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

  return (
    <div className="p-5 space-y-4 max-w-[1400px] mx-auto animate-fade-in">
      {confirm && (
        <ConfirmDialog
          title={confirm.action === 'approve' ? `Approve ${confirm.ticker}?` : `Reject ${confirm.ticker}?`}
          message={
            confirm.action === 'approve'
              ? 'This will send the order to the execution engine for processing.'
              : 'This will reject the sizing recommendation and remove it from the queue.'
          }
          confirmLabel={confirm.action === 'approve' ? 'Approve' : 'Reject'}
          destructive={confirm.action === 'reject'}
          onConfirm={handleConfirm}
          onCancel={() => setConfirm(null)}
        />
      )}

      {/* ── CRITICAL banner ───────────────────────────────────────── */}
      {criticals.length > 0 && (
        <div
          className="rounded-lg px-5 py-3 flex items-center gap-3 cursor-pointer transition-opacity hover:opacity-90"
          style={{
            background:  'var(--red-bg)',
            border:      '1px solid var(--red-border)',
          }}
          onClick={() => navigate('/risk')}
        >
          <span className="w-2.5 h-2.5 rounded-full flex-shrink-0 dot-red pulse-critical" />
          <span className="font-bold text-sm tracking-wide" style={{ color: 'var(--red)', fontFamily: 'Syne' }}>
            CRITICAL ALERT — Trade approvals blocked.
          </span>
          <span className="text-[13px]" style={{ color: 'var(--text)' }}>
            {criticals[0]?.message}
          </span>
          <span className="ml-auto text-[11px] font-bold underline" style={{ color: 'var(--red)' }}>
            View Risk →
          </span>
        </div>
      )}

      {/* ── P&L Strip ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <PnLCard
          label="Portfolio Value"
          value={fmt$(portfolioValue)}
          sub={ibkrOk ? (isPaper ? 'Paper · live IBKR' : 'Live · from IBKR') : 'IBKR disconnected'}
        />
        <PnLCard label="Cash Available" value={fmt$(cashBalance)} sub="Buying power" />
        <PnLCard
          label="Unrealized P&L"
          value={fmt$(unrealizedPnl)}
          sub="Open positions"
          colorVar={pnlColor(unrealizedPnl)}
        />
        <PnLCard
          label="Realized P&L"
          value={fmt$(realizedPnl)}
          sub="Session closed"
          colorVar={pnlColor(realizedPnl)}
        />
      </div>

      {/* ── System health strip ───────────────────────────────────── */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-2">
        <HealthPill
          ok={ibkrOk}
          label="IBKR Gateway"
          sub={ibkrOk ? (isPaper ? 'Paper' : 'Live') : 'Disconnected'}
          onClick={() => navigate('/execution')}
        />
        <HealthPill
          ok={briefing?.created_at ? true : null}
          label="Macro Engine"
          sub={briefing?.created_at ? fmtAgo(briefing.created_at) : 'No data'}
          onClick={() => navigate('/macro')}
        />
        <HealthPill ok={true} label="Screener" sub="Ready" onClick={() => navigate('/screener')} />
        <HealthPill
          ok={alerts.length === 0 && criticals.length === 0}
          label="Risk Monitor"
          sub={criticals.length > 0 ? `${criticals.length} critical` : alerts.length > 0 ? `${alerts.length} alerts` : 'All clear'}
          badge={criticals.length || undefined}
          badgeStyle={{ background: 'var(--red-bg)', color: 'var(--red)' }}
          onClick={() => navigate('/risk')}
        />
        <HealthPill
          ok={pending.length === 0}
          label="Pending"
          sub={pending.length > 0 ? 'Awaiting review' : 'None'}
          badge={pending.length || undefined}
          badgeStyle={{ background: 'var(--amber-bg)', color: 'var(--amber)' }}
          onClick={() => navigate('/portfolio')}
        />
        <HealthPill
          ok={regimeKey != null}
          label="Regime"
          sub={regimeKey ?? 'Unknown'}
          onClick={() => navigate('/macro')}
        />
      </div>

      {/* ── Regime card ───────────────────────────────────────────── */}
      {regCfg ? (
        <div
          className={`rounded-lg border p-5 ${regCfg.cls}`}
          style={{ borderWidth: '1px' }}
        >
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-4">
              <span className={`w-3 h-3 rounded-full ${regCfg.dotCls}`} />
              <div>
                <div className="section-label mb-0.5">Market Regime</div>
                <div
                  className="text-[22px] font-black tracking-tight leading-none"
                  style={{ color: regCfg.colorVar, fontFamily: 'Syne' }}
                >
                  {regimeKey}
                </div>
              </div>
              {regime?.regime_confidence != null && (
                <div
                  className="text-sm px-3 py-1 rounded-md font-data"
                  style={{
                    background: 'var(--accent-muted)',
                    color:      'var(--accent)',
                    border:     '1px solid var(--accent-ring)',
                    fontFamily: 'JetBrains Mono',
                  }}
                >
                  {regime.regime_confidence}/10 confidence
                </div>
              )}
            </div>

            <div className="flex gap-8">
              {SUB_SCORES.map(({ key, label }) => (
                <SubScore key={key} label={label} value={regime?.[key] ?? briefing?.[key]} />
              ))}
            </div>

            <button
              onClick={() => navigate('/macro')}
              className="text-[11px] font-bold tracking-wide transition-opacity hover:opacity-70"
              style={{ color: regCfg.colorVar, fontFamily: 'Syne' }}
            >
              Full Macro →
            </button>
          </div>
        </div>
      ) : (
        <div
          className="rounded-lg p-5"
          style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
        >
          <span className="text-[11px]" style={{ color: 'var(--text-3)' }}>
            No regime data — run macro agent.
          </span>
        </div>
      )}

      {/* ── Equity curve + Pending approvals ──────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* Equity curve */}
        <div
          className="lg:col-span-3 rounded-lg p-5"
          style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
        >
          <div className="flex items-center justify-between mb-4">
            <div className="section-label">Portfolio Equity Curve</div>
            <span className="text-[10px] font-data" style={{ color: 'var(--text-3)' }}>
              Based on closed fills
            </span>
          </div>
          <EquityCurveChart data={[]} height={180} />
        </div>

        {/* Pending approvals */}
        <div
          className="lg:col-span-2 rounded-lg p-5"
          style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
        >
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <div className="section-label">Awaiting Approval</div>
              {pending.length > 0 && (
                <span
                  className="text-[10px] font-bold px-1.5 py-0.5 rounded-sm font-data"
                  style={{ background: 'var(--amber-bg)', color: 'var(--amber)' }}
                >
                  {pending.length}
                </span>
              )}
            </div>
            {pending.length > 3 && (
              <button
                onClick={() => navigate('/portfolio')}
                className="text-[11px] font-bold transition-opacity hover:opacity-70"
                style={{ color: 'var(--accent)', fontFamily: 'Syne' }}
              >
                See All →
              </button>
            )}
          </div>

          {pending.length === 0 ? (
            <div
              className="flex flex-col items-center justify-center gap-2"
              style={{ height: 160, color: 'var(--text-3)' }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '28px', color: 'var(--border-2)' }}>
                check_circle
              </span>
              <p className="text-[12px]">No pending approvals</p>
            </div>
          ) : (
            <div className="space-y-2.5">
              {pending.slice(0, 3).map(item => {
                const vs = VERDICT_STYLES[item.verdict] || VERDICT_STYLES.AVOID;
                return (
                  <div
                    key={item.id}
                    className="rounded-md p-3"
                    style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}
                  >
                    <div className="flex items-center gap-2 mb-2.5">
                      <span
                        className="font-bold text-sm font-data"
                        style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }}
                      >
                        {item.ticker}
                      </span>
                      <span
                        className="text-[10px] font-bold px-1.5 py-0.5 rounded-sm"
                        style={{ background: vs.bgVar, color: vs.colorVar, fontFamily: 'Syne' }}
                      >
                        {item.verdict}
                      </span>
                      <ConvictionBadge score={item.conviction_score} />
                      {item.size_label && (
                        <span className="ml-auto text-[10px] font-data" style={{ color: 'var(--text-2)' }}>
                          {item.size_label}
                        </span>
                      )}
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => setConfirm({ action: 'approve', id: item.id, ticker: item.ticker })}
                        className="flex-1 py-1.5 text-[11px] font-bold rounded-md transition-opacity hover:opacity-80"
                        style={{
                          background: 'var(--green-bg)',
                          border:     '1px solid var(--green-border)',
                          color:      'var(--green)',
                          fontFamily: 'Syne',
                        }}
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => setConfirm({ action: 'reject', id: item.id, ticker: item.ticker })}
                        className="flex-1 py-1.5 text-[11px] font-bold rounded-md transition-all"
                        style={{
                          background: 'var(--surface)',
                          border:     '1px solid var(--border)',
                          color:      'var(--text-2)',
                          fontFamily: 'Syne',
                        }}
                        onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--red-border)'; e.currentTarget.style.color = 'var(--red)'; }}
                        onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-2)'; }}
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Alerts + Recent Memos ──────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Risk alerts */}
        <div
          className="rounded-lg p-5"
          style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
        >
          <div className="flex items-center justify-between mb-4">
            <div className="section-label">Recent Risk Alerts</div>
            <button
              onClick={() => navigate('/risk')}
              className="text-[11px] font-bold transition-opacity hover:opacity-70"
              style={{ color: 'var(--accent)', fontFamily: 'Syne' }}
            >
              See All →
            </button>
          </div>
          {alerts.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2" style={{ height: 96 }}>
              <span className="material-symbols-outlined" style={{ fontSize: '24px', color: 'var(--border-2)' }}>
                shield
              </span>
              <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>No active alerts</p>
            </div>
          ) : (
            <div className="space-y-2">
              {alerts.map(a => <RiskAlert key={a.id} alert={a} compact />)}
            </div>
          )}
        </div>

        {/* Research memos */}
        <div
          className="rounded-lg p-5"
          style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
        >
          <div className="flex items-center justify-between mb-4">
            <div className="section-label">Recent Research</div>
            <button
              onClick={() => navigate('/research')}
              className="text-[11px] font-bold transition-opacity hover:opacity-70"
              style={{ color: 'var(--accent)', fontFamily: 'Syne' }}
            >
              See All →
            </button>
          </div>
          {memoHistory.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2" style={{ height: 96 }}>
              <span className="material-symbols-outlined" style={{ fontSize: '24px', color: 'var(--border-2)' }}>
                query_stats
              </span>
              <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>No research memos yet</p>
            </div>
          ) : (
            <div className="space-y-1.5">
              {memoHistory.map((memo, i) => {
                const vs = VERDICT_STYLES[memo.verdict] || VERDICT_STYLES.AVOID;
                return (
                  <div
                    key={memo.id ?? i}
                    className="flex items-center gap-3 px-3 py-2.5 rounded-md cursor-pointer transition-colors"
                    onClick={() => navigate('/research')}
                    onMouseEnter={e => {
                      e.currentTarget.style.background = 'var(--surface-2)';
                    }}
                    onMouseLeave={e => {
                      e.currentTarget.style.background = 'transparent';
                    }}
                  >
                    <span
                      className="font-bold text-[13px] font-data w-16 flex-shrink-0"
                      style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }}
                    >
                      {memo.ticker}
                    </span>
                    <span
                      className="text-[10px] font-bold px-1.5 py-0.5 rounded-sm"
                      style={{ background: vs.bgVar, color: vs.colorVar, fontFamily: 'Syne' }}
                    >
                      {memo.verdict}
                    </span>
                    <ConvictionBadge score={memo.conviction_score} />
                    <span className="ml-auto text-[10px] font-data" style={{ color: 'var(--text-3)' }}>
                      {memo.date
                        ? new Date(memo.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                        : ''}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
