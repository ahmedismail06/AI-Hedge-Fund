import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { getPositions, getPending, approveTrade, rejectTrade, getEquityCurve } from '../api/portfolio';
import { getAlerts, getCriticalAlerts, getMetrics } from '../api/risk';
import { getRegime, getBriefing } from '../api/macro';
import { getExecutionStatus } from '../api/execution';
import { getHistory } from '../api/research';
import { getPMDecisions } from '../api/pm';
import EquityCurveChart from '../components/EquityCurveChart';
import RiskAlert from '../components/RiskAlert';
import ConfirmDialog from '../components/ConfirmDialog';
import ConvictionBadge from '../components/ConvictionBadge';

/* ─── Regime config ─────────────────────────────────────────────── */
const REGIME_CONFIG = {
  'Risk-On':     { cls: 'regime-risk-on',     dotCls: 'dot-green', colorVar: 'var(--regime-on-text)' },
  'Risk-Off':    { cls: 'regime-risk-off',    dotCls: 'dot-red',   colorVar: 'var(--regime-off-text)' },
  'Stagflation': { cls: 'regime-stagflation', dotCls: 'dot-amber', colorVar: 'var(--regime-st-text)' },
  'Transitional':{ cls: 'regime-transitional',dotCls: 'dot-blue',  colorVar: 'var(--regime-tr-text)' },
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

const DECISION_STATUS_STYLE = {
  SENT_TO_EXECUTION:  { color: 'var(--green)',  bg: 'var(--green-bg)' },
  BLOCKED:            { color: 'var(--red)',    bg: 'var(--red-bg)' },
  DEFERRED:           { color: 'var(--amber)',  bg: 'var(--amber-bg)' },
  PENDING_HUMAN:      { color: 'var(--amber)',  bg: 'var(--amber-bg)' },
  NO_ACTION:          { color: 'var(--text-3)', bg: 'var(--surface-2)' },
  TRIGGERED_PIPELINE: { color: 'var(--accent)', bg: 'var(--accent-muted)' },
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
  if (!ts) return '—';
  const diff = (Date.now() - new Date(ts)) / 1000;
  if (diff < 60)    return 'just now';
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

/* ─── Section Label ──────────────────────────────────────────────── */
function SectionLabel({ children }) {
  return (
    <div
      className="text-[9px] font-bold tracking-[0.18em] uppercase"
      style={{ color: 'var(--text-3)', fontFamily: 'Syne' }}
    >
      {children}
    </div>
  );
}

/* ─── Metric Block ───────────────────────────────────────────────── */
function Metric({ label, value, sub, color }) {
  return (
    <div className="flex flex-col gap-0.5">
      <SectionLabel>{label}</SectionLabel>
      <div
        className="text-[22px] font-bold leading-none"
        style={{ color: color ?? 'var(--text)', fontFamily: 'JetBrains Mono' }}
      >
        {value}
      </div>
      {sub && (
        <div className="text-[10px]" style={{ color: 'var(--text-3)' }}>{sub}</div>
      )}
    </div>
  );
}

/* ─── Status Chip (inline system status ribbon) ──────────────────── */
function StatusChip({ ok, label, sub, badge, onClick }) {
  const dotColor  = ok === true  ? 'var(--green)'              : ok === false ? 'var(--red)'              : 'var(--text-3)';
  const glowColor = ok === true  ? 'rgba(0,217,138,0.55)'      : ok === false ? 'rgba(255,51,71,0.55)'    : 'none';

  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 transition-opacity hover:opacity-70 flex-shrink-0"
      style={{ cursor: onClick ? 'pointer' : 'default' }}
    >
      <span
        className="w-1.5 h-1.5 rounded-full flex-shrink-0"
        style={{ background: dotColor, boxShadow: ok != null ? `0 0 5px ${glowColor}` : 'none' }}
      />
      <div className="flex items-baseline gap-1">
        <span
          className="text-[11px] font-bold"
          style={{ color: 'var(--text)', fontFamily: 'Syne' }}
        >
          {label}
        </span>
        {sub && (
          <span className="text-[10px]" style={{ color: 'var(--text-3)' }}>{sub}</span>
        )}
      </div>
      {badge != null && badge > 0 && (
        <span
          className="text-[9px] font-bold px-1 py-px rounded"
          style={{ background: 'var(--red-bg)', color: 'var(--red)', border: '1px solid var(--red-border)' }}
        >
          {badge}
        </span>
      )}
    </button>
  );
}

/* ─── Score Pill (regime sub-scores) ────────────────────────────── */
function ScorePill({ label, value }) {
  if (value == null) return null;
  const n     = parseFloat(value);
  const color = n >= 7 ? 'var(--green)' : n >= 4 ? 'var(--amber)' : 'var(--red)';
  const bg    = n >= 7 ? 'var(--green-bg)' : n >= 4 ? 'var(--amber-bg)' : 'var(--red-bg)';
  return (
    <div className="flex flex-col items-center gap-1">
      <SectionLabel>{label}</SectionLabel>
      <div
        className="text-[15px] font-bold px-2.5 py-0.5 rounded-lg"
        style={{ color, background: bg, fontFamily: 'JetBrains Mono' }}
      >
        {n.toFixed(1)}
      </div>
    </div>
  );
}

/* ─── Card Shell ─────────────────────────────────────────────────── */
function Card({ children, className = '', style = {} }) {
  return (
    <div
      className={`rounded-xl p-5 ${className}`}
      style={{ background: 'var(--surface)', border: '1px solid var(--border)', ...style }}
    >
      {children}
    </div>
  );
}

/* ─── Card Header ────────────────────────────────────────────────── */
function CardHeader({ label, title, action, onAction }) {
  return (
    <div className="flex items-start justify-between mb-4">
      <div>
        <SectionLabel>{label}</SectionLabel>
        {title && (
          <div className="text-[13px] font-semibold mt-0.5" style={{ color: 'var(--text)' }}>
            {title}
          </div>
        )}
      </div>
      {action && (
        <button
          onClick={onAction}
          className="text-[11px] font-bold transition-opacity hover:opacity-70 flex-shrink-0"
          style={{ color: 'var(--accent)', fontFamily: 'Syne' }}
        >
          {action}
        </button>
      )}
    </div>
  );
}

/* ─── Empty State ────────────────────────────────────────────────── */
function EmptyState({ icon, label, sub, onClick, iconColor }) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-2"
      style={{ height: 110, cursor: onClick ? 'pointer' : 'default' }}
      onClick={onClick}
    >
      <span
        className="material-symbols-outlined"
        style={{ fontSize: '26px', color: iconColor ?? 'var(--border-2)' }}
      >
        {icon}
      </span>
      <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>{label}</p>
      {sub && <p className="text-[11px]" style={{ color: 'var(--text-3)' }}>{sub}</p>}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════ */
export default function Dashboard() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();
  const [positions,   setPositions]   = useState([]);
  const [pending,     setPending]     = useState([]);
  const [alerts,      setAlerts]      = useState([]);
  const [criticals,   setCriticals]   = useState([]);
  const [metrics,     setMetrics]     = useState(null);
  const [regime,      setRegime]      = useState(null);
  const [briefing,    setBriefing]    = useState(null);
  const [execStatus,  setExecStatus]  = useState(null);
  const [memoHistory, setMemoHistory] = useState([]);
  const [equityCurve, setEquityCurve] = useState([]);
  const [pmDecisions, setPmDecisions] = useState([]);
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

  const loadEquityCurve = useCallback(async () => {
    try {
      const data = await getEquityCurve(30);
      setEquityCurve(Array.isArray(data) ? data : []);
    } catch {}
  }, []);

  const loadPmDecisions = useCallback(async () => {
    try {
      const data = await getPMDecisions({ limit: 5 });
      setPmDecisions(Array.isArray(data) ? data : []);
    } catch {}
  }, []);

  useEffect(() => {
    loadPnL(); loadAlerts(); loadHealth(); loadMemos(); loadEquityCurve(); loadPmDecisions();
    const t1 = setInterval(loadPnL,          60_000);
    const t2 = setInterval(loadAlerts,        30_000);
    const t3 = setInterval(loadHealth,       300_000);
    const t4 = setInterval(loadEquityCurve,  300_000);
    const t5 = setInterval(loadPmDecisions,   60_000);
    return () => { clearInterval(t1); clearInterval(t2); clearInterval(t3); clearInterval(t4); clearInterval(t5); };
  }, [loadPnL, loadAlerts, loadHealth, loadMemos, loadEquityCurve, loadPmDecisions]);

  const regimeKey = regime?.regime ?? briefing?.regime;
  const regCfg    = REGIME_CONFIG[regimeKey] ?? null;
  const ibkrOk    = execStatus?.ibkr_connected === true;
  const isPaper   = execStatus?.is_paper === true;

  const portfolioValue = execStatus?.net_liquidation ?? null;
  const cashBalance    = execStatus?.cash             ?? null;
  const unrealizedPnl  = execStatus?.unrealized_pnl   ?? null;
  const realizedPnl    = execStatus?.realized_pnl     ?? null;

  const pnlColor = (v) => v == null ? 'var(--text)' : v >= 0 ? 'var(--green)' : 'var(--red)';
  const riskClear = alerts.length === 0 && criticals.length === 0;

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
    <div className="p-6 space-y-4 max-w-[1400px] mx-auto animate-fade-in">
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

      {/* ── Critical Banner ───────────────────────────────────────── */}
      {criticals.length > 0 && (
        <div
          className="rounded-xl px-5 py-3.5 flex items-center gap-3 cursor-pointer transition-opacity hover:opacity-90"
          style={{ background: 'var(--red-bg)', border: '1px solid var(--red-border)' }}
          onClick={() => navigate('/risk')}
        >
          <span className="w-2 h-2 rounded-full flex-shrink-0 dot-red pulse-critical" />
          <span
            className="font-bold text-[12px] tracking-widest uppercase flex-shrink-0"
            style={{ color: 'var(--red)', fontFamily: 'Syne' }}
          >
            Critical Alert
          </span>
          <span className="w-px h-4 flex-shrink-0" style={{ background: 'var(--red-border)' }} />
          <span className="text-[12px] truncate" style={{ color: 'var(--text)' }}>
            {criticals[0]?.message}
          </span>
          <span
            className="ml-auto text-[11px] font-bold flex-shrink-0"
            style={{ color: 'var(--red)', fontFamily: 'Syne' }}
          >
            View Risk →
          </span>
        </div>
      )}

      {/* ── Portfolio Hero Card ───────────────────────────────────── */}
      <Card>
        {/* Top row: Big number + PnL metrics */}
        <div className="flex flex-wrap items-start justify-between gap-6">

          {/* Portfolio value */}
          <div className="flex flex-col gap-2">
            <SectionLabel>Portfolio Value</SectionLabel>
            <div
              className="text-[40px] font-black leading-none tracking-tight"
              style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }}
            >
              {fmt$(portfolioValue)}
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <span
                className="text-[10px] font-bold px-2 py-0.5 rounded-md"
                style={{
                  background: ibkrOk ? 'var(--green-bg)'   : 'var(--surface-2)',
                  color:      ibkrOk ? 'var(--green)'       : 'var(--text-3)',
                  border:     `1px solid ${ibkrOk ? 'var(--green-border)' : 'var(--border)'}`,
                  fontFamily: 'Syne',
                }}
              >
                {ibkrOk ? (isPaper ? '● PAPER' : '● LIVE') : '○ DISCONNECTED'}
              </span>
              {regCfg && (
                <span
                  className="text-[10px] font-bold px-2 py-0.5 rounded-md"
                  style={{
                    color:      regCfg.colorVar,
                    background: 'var(--surface-2)',
                    border:     '1px solid var(--border)',
                    fontFamily: 'Syne',
                  }}
                >
                  {regimeKey}
                </span>
              )}
              {pending.length > 0 && (
                <button
                  onClick={() => navigate('/portfolio')}
                  className="flex items-center gap-1.5 text-[10px] font-bold px-2 py-0.5 rounded-md transition-opacity hover:opacity-75"
                  style={{
                    background: 'var(--amber-bg)',
                    border:     '1px solid var(--amber-border)',
                    color:      'var(--amber)',
                    fontFamily: 'Syne',
                  }}
                >
                  <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--amber)' }} />
                  {pending.length} awaiting approval
                </button>
              )}
            </div>
          </div>

          {/* PnL metrics */}
          <div className="flex flex-wrap gap-8 pt-1">
            <Metric label="Cash" value={fmt$(cashBalance)} sub="Buying power" />
            <Metric
              label="Unrealized P&L"
              value={fmt$(unrealizedPnl)}
              sub="Open positions"
              color={pnlColor(unrealizedPnl)}
            />
            <Metric
              label="Realized P&L"
              value={fmt$(realizedPnl)}
              sub="Session closed"
              color={pnlColor(realizedPnl)}
            />
            {positions.length > 0 && (
              <Metric label="Positions" value={positions.length} sub="Open" />
            )}
          </div>
        </div>

        {/* Divider */}
        <div className="my-4" style={{ borderTop: '1px solid var(--border)' }} />

        {/* System Status Ribbon */}
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <SectionLabel>Systems</SectionLabel>
          <StatusChip
            ok={ibkrOk}
            label="IBKR"
            sub={ibkrOk ? (isPaper ? 'paper' : 'live') : 'disconnected'}
            onClick={() => navigate('/execution')}
          />
          <StatusChip
            ok={(briefing?.date || regime?.date) ? true : null}
            label="Macro"
            sub={fmtAgo(briefing?.date || regime?.date)}
            onClick={() => navigate('/macro')}
          />
          <StatusChip
            ok={true}
            label="Screener"
            sub="ready"
            onClick={() => navigate('/screener')}
          />
          <StatusChip
            ok={riskClear}
            label="Risk"
            sub={
              criticals.length > 0
                ? `${criticals.length} critical`
                : alerts.length > 0
                ? `${alerts.length} alerts`
                : 'clear'
            }
            badge={criticals.length || undefined}
            onClick={() => navigate('/risk')}
          />
          <StatusChip
            ok={regimeKey != null}
            label="Regime"
            sub={regimeKey?.toLowerCase() ?? 'unknown'}
            onClick={() => navigate('/macro')}
          />
        </div>
      </Card>

      {/* ── Regime Card ───────────────────────────────────────────── */}
      {regCfg ? (
        <div
          className={`rounded-xl px-5 py-4 ${regCfg.cls}`}
          style={{ borderWidth: '1px' }}
        >
          <div className="flex flex-wrap items-center justify-between gap-4">

            <div className="flex items-center gap-3">
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${regCfg.dotCls}`} />
              <div>
                <SectionLabel>Market Regime</SectionLabel>
                <div
                  className="text-[20px] font-black tracking-tight leading-tight mt-0.5"
                  style={{ color: regCfg.colorVar, fontFamily: 'Syne' }}
                >
                  {regimeKey}
                </div>
              </div>
              {regime?.regime_confidence != null && (
                <div
                  className="text-[11px] px-3 py-1 rounded-lg ml-1"
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

            <div className="flex gap-6">
              {SUB_SCORES.map(({ key, label }) => (
                <ScorePill key={key} label={label} value={regime?.[key] ?? briefing?.[key]} />
              ))}
            </div>

            <button
              onClick={() => navigate('/macro')}
              className="text-[11px] font-bold tracking-wide transition-opacity hover:opacity-70 flex-shrink-0"
              style={{ color: regCfg.colorVar, fontFamily: 'Syne' }}
            >
              Full Macro →
            </button>
          </div>
        </div>
      ) : (
        <Card>
          <p className="text-[12px]" style={{ color: 'var(--text-3)' }}>
            No regime data — run macro agent.
          </p>
        </Card>
      )}

      {/* ── Equity Curve + PM Activity ────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">

        {/* Equity curve */}
        <Card className="lg:col-span-3">
          <CardHeader
            label="Equity Curve"
            title="30-Day NAV"
            action="Portfolio →"
            onAction={() => navigate('/portfolio')}
          />
          <EquityCurveChart data={equityCurve} height={200} />
        </Card>

        {/* PM Activity / Pending Approvals */}
        <Card className="lg:col-span-2">
          <CardHeader
            label={pending.length > 0 ? 'Awaiting Approval' : 'PM Activity'}
            title={
              pending.length > 0
                ? <span style={{ color: 'var(--amber)' }}>{pending.length} pending decision{pending.length !== 1 ? 's' : ''}</span>
                : 'Recent decisions'
            }
            action="See All →"
            onAction={() => navigate(pending.length > 0 ? '/portfolio' : '/orchestrator')}
          />

          {pending.length === 0 ? (
            pmDecisions.length === 0 ? (
              <EmptyState
                icon="smart_toy"
                label="No decisions yet"
                sub="View audit log →"
                onClick={() => navigate('/orchestrator')}
              />
            ) : (
              <div className="space-y-0.5">
                {pmDecisions.map((d, i) => {
                  const st = DECISION_STATUS_STYLE[d.execution_status] ?? { color: 'var(--text-3)', bg: 'var(--surface-2)' };
                  return (
                    <div
                      key={d.decision_id ?? i}
                      className="flex items-center gap-2.5 px-3 py-2.5 rounded-lg cursor-pointer transition-colors"
                      onClick={() => navigate('/orchestrator')}
                      onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-2)'; }}
                      onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                    >
                      <span
                        className="font-bold text-[12px] w-14 flex-shrink-0 truncate"
                        style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }}
                      >
                        {d.ticker ?? '—'}
                      </span>
                      <span
                        className="text-[9px] font-bold px-1.5 py-0.5 rounded flex-shrink-0"
                        style={{ background: 'var(--surface-2)', color: 'var(--text-2)', border: '1px solid var(--border)', fontFamily: 'Syne' }}
                      >
                        {d.category}
                      </span>
                      <span className="text-[11px] truncate flex-1" style={{ color: 'var(--text-2)' }}>
                        {d.decision}
                      </span>
                      <span
                        className="text-[9px] font-bold px-1.5 py-0.5 rounded flex-shrink-0"
                        style={{ background: st.bg, color: st.color, fontFamily: 'Syne' }}
                      >
                        {d.execution_status?.replace(/_/g, ' ')}
                      </span>
                      <span
                        className="text-[10px] flex-shrink-0"
                        style={{ color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
                      >
                        {d.timestamp ? fmtAgo(d.timestamp) : ''}
                      </span>
                    </div>
                  );
                })}
              </div>
            )
          ) : (
            <div className="space-y-2.5">
              {pending.slice(0, 3).map(item => {
                const vs = VERDICT_STYLES[item.verdict] || VERDICT_STYLES.AVOID;
                return (
                  <div
                    key={item.id}
                    className="rounded-xl p-4"
                    style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}
                  >
                    <div className="flex items-center gap-2 mb-3">
                      <span
                        className="font-bold text-[14px]"
                        style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }}
                      >
                        {item.ticker}
                      </span>
                      <span
                        className="text-[10px] font-bold px-2 py-0.5 rounded-md"
                        style={{ background: vs.bgVar, color: vs.colorVar, fontFamily: 'Syne' }}
                      >
                        {item.verdict}
                      </span>
                      <ConvictionBadge score={item.conviction_score} />
                      {item.size_label && (
                        <span className="ml-auto text-[11px]" style={{ color: 'var(--text-2)' }}>
                          {item.size_label}
                        </span>
                      )}
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <button
                        onClick={() => setConfirm({ action: 'approve', id: item.id, ticker: item.ticker })}
                        disabled={!isAdmin}
                        title={!isAdmin ? 'Guest mode — read only' : undefined}
                        className="py-2 text-[11px] font-bold rounded-lg transition-opacity hover:opacity-85 disabled:opacity-40 disabled:cursor-not-allowed"
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
                        disabled={!isAdmin}
                        title={!isAdmin ? 'Guest mode — read only' : undefined}
                        className="py-2 text-[11px] font-bold rounded-lg transition-all disabled:opacity-40 disabled:cursor-not-allowed"
                        style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text-2)', fontFamily: 'Syne' }}
                        onMouseEnter={e => { if (isAdmin) { e.currentTarget.style.borderColor = 'var(--red-border)'; e.currentTarget.style.color = 'var(--red)'; } }}
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
        </Card>
      </div>

      {/* ── Risk Alerts + Recent Research ─────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

        {/* Risk Alerts */}
        <Card>
          <CardHeader
            label="Risk Monitor"
            title={
              criticals.length > 0
                ? <span style={{ color: 'var(--red)' }}>{criticals.length} Critical</span>
                : alerts.length > 0
                ? <span style={{ color: 'var(--amber)' }}>{alerts.length} Active</span>
                : <span style={{ color: 'var(--green)' }}>All Clear</span>
            }
            action="See All →"
            onAction={() => navigate('/risk')}
          />
          {alerts.length === 0 ? (
            <EmptyState
              icon="shield"
              label="No active alerts"
              iconColor={riskClear ? 'var(--green)' : 'var(--border-2)'}
            />
          ) : (
            <div className="space-y-2">
              {alerts.map(a => <RiskAlert key={a.id} alert={a} compact />)}
            </div>
          )}
        </Card>

        {/* Research Memos */}
        <Card>
          <CardHeader
            label="Recent Research"
            title={memoHistory.length > 0 ? `${memoHistory.length} memos` : 'No memos yet'}
            action="See All →"
            onAction={() => navigate('/research')}
          />
          {memoHistory.length === 0 ? (
            <EmptyState icon="query_stats" label="No research memos yet" />
          ) : (
            <div className="space-y-0.5">
              {memoHistory.map((memo, i) => {
                const vs = VERDICT_STYLES[memo.verdict] || VERDICT_STYLES.AVOID;
                return (
                  <div
                    key={memo.id ?? i}
                    className="flex items-center gap-3 px-3 py-2.5 rounded-lg cursor-pointer transition-colors"
                    onClick={() => navigate('/research')}
                    onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-2)'; }}
                    onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                  >
                    <span
                      className="font-bold text-[13px] w-16 flex-shrink-0"
                      style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }}
                    >
                      {memo.ticker}
                    </span>
                    <span
                      className="text-[10px] font-bold px-1.5 py-0.5 rounded-md"
                      style={{ background: vs.bgVar, color: vs.colorVar, fontFamily: 'Syne' }}
                    >
                      {memo.verdict}
                    </span>
                    <ConvictionBadge score={memo.conviction_score} />
                    <span
                      className="ml-auto text-[11px] flex-shrink-0"
                      style={{ color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
                    >
                      {memo.date
                        ? new Date(memo.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                        : ''}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
