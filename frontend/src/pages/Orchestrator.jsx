import { useEffect, useState } from 'react';
import { getPMStatus, getPMDecisions, overrideDecision, haltPM, resumePM, runPMCycle } from '../api/pm';
import ConfirmDialog from '../components/ConfirmDialog';

const CATEGORY_BADGE_STYLE = {
  NEW_ENTRY:    { bg: 'var(--green-bg)',      color: 'var(--green)',   border: 'var(--green-border)' },
  EXIT_TRIM:    { bg: 'var(--amber-bg)',      color: 'var(--amber)',   border: 'var(--amber-border)' },
  REBALANCE:    { bg: 'var(--blue-bg)',       color: 'var(--blue)',    border: 'var(--blue-border)' },
  CRISIS:       { bg: 'var(--red-bg)',        color: 'var(--red)',     border: 'var(--red-border)' },
  PRE_EARNINGS: { bg: 'var(--accent-muted)',  color: 'var(--accent)',  border: 'var(--accent-ring)' },
};

const DECISION_BADGE_STYLE = {
  EXECUTE:             { bg: 'var(--green-bg)',    color: 'var(--green)' },
  MODIFY_SIZE:         { bg: 'var(--green-bg)',    color: 'var(--green)' },
  HOLD:                { bg: 'var(--surface-2)',   color: 'var(--text-2)' },
  NO_ACTION:           { bg: 'var(--surface-2)',   color: 'var(--text-3)' },
  DEFER:               { bg: 'var(--amber-bg)',    color: 'var(--amber)' },
  REJECT:              { bg: 'var(--red-bg)',      color: 'var(--red)' },
  WATCHLIST:           { bg: 'var(--accent-muted)',color: 'var(--accent)' },
  TRIM:                { bg: 'var(--amber-bg)',    color: 'var(--amber)' },
  CLOSE:               { bg: 'var(--red-bg)',      color: 'var(--red)' },
  ADD:                 { bg: 'var(--green-bg)',    color: 'var(--green)' },
  REDUCE_EXPOSURE:     { bg: 'var(--amber-bg)',    color: 'var(--amber)' },
  HALT_NEW_ENTRIES:    { bg: 'var(--red-bg)',      color: 'var(--red)' },
  LIQUIDATE_TO_TARGET: { bg: 'var(--red-bg)',      color: 'var(--red)' },
  MONITOR:             { bg: 'var(--amber-bg)',    color: 'var(--amber)' },
  REBALANCE:           { bg: 'var(--blue-bg)',     color: 'var(--blue)' },
  RAISE_CASH:          { bg: 'var(--amber-bg)',    color: 'var(--amber)' },
  DEPLOY_CASH:         { bg: 'var(--green-bg)',    color: 'var(--green)' },
  SIZE_UP:             { bg: 'var(--green-bg)',    color: 'var(--green)' },
  EXIT:                { bg: 'var(--red-bg)',      color: 'var(--red)' },
};

const EXEC_STATUS_STYLE = {
  SENT_TO_EXECUTION: { bg: 'var(--green-bg)',  color: 'var(--green)' },
  BLOCKED:           { bg: 'var(--red-bg)',    color: 'var(--red)' },
  DEFERRED:          { bg: 'var(--amber-bg)',  color: 'var(--amber)' },
  NO_ACTION:         { bg: 'var(--surface-2)', color: 'var(--text-3)' },
  PENDING_HUMAN:     { bg: 'var(--blue-bg)',   color: 'var(--blue)' },
};

const FALLBACK_BADGE = { bg: 'var(--surface-2)', color: 'var(--text-3)', border: 'var(--border)' };

const ALL_CATEGORIES = ['NEW_ENTRY', 'EXIT_TRIM', 'REBALANCE', 'CRISIS', 'PRE_EARNINGS'];

const SL_LABEL = {
  fontSize: '9px',
  fontWeight: 700,
  letterSpacing: '0.18em',
  textTransform: 'uppercase',
  color: 'var(--text-3)',
  fontFamily: 'Syne',
};

function formatTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatDateTime(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function ConfidenceBar({ value }) {
  if (value == null) return <span style={{ color: 'var(--text-3)' }}>—</span>;
  const pct = Math.round(value * 100);
  const barColor = pct >= 70 ? 'var(--green)' : pct >= 50 ? 'var(--amber)' : 'var(--red)';
  return (
    <div className="flex items-center gap-2">
      <div
        className="w-16 rounded-full overflow-hidden"
        style={{ height: '6px', background: 'var(--border)' }}
      >
        <div style={{ width: `${pct}%`, height: '100%', background: barColor }} />
      </div>
      <span style={{ fontSize: '12px', color: 'var(--text-2)', fontFamily: 'JetBrains Mono' }}>{pct}%</span>
    </div>
  );
}

const DEFER_OPTIONS = [
  { label: '1 Day',   value: '1' },
  { label: '3 Days',  value: '3' },
  { label: '1 Week',  value: '7' },
  { label: 'Custom…', value: 'custom' },
];

function DeferModal({ target, onConfirm, onCancel }) {
  const [duration, setDuration] = useState('3');
  const [customDate, setCustomDate] = useState('');
  const [condition, setCondition] = useState('');

  const deferUntil = duration === 'custom' ? customDate : duration;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div
        className="w-full max-w-md p-6 space-y-4 rounded-2xl"
        style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
      >
        <h2 style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text)', fontFamily: 'Syne' }}>
          Defer Decision
        </h2>
        <p style={{ fontSize: '13px', color: 'var(--text-3)' }}>
          Set when to re-evaluate{' '}
          <span style={{ fontFamily: 'JetBrains Mono', fontWeight: 600, color: 'var(--text-2)' }}>
            {target.ticker || 'this decision'}
          </span>
          . The research scheduler will re-queue it automatically on that date.
        </p>

        <div>
          <label style={SL_LABEL}>Re-check in</label>
          <div className="flex gap-2 mt-1.5 flex-wrap">
            {DEFER_OPTIONS.map(opt => {
              const isSelected = duration === opt.value;
              return (
                <button
                  key={opt.value}
                  onClick={() => setDuration(opt.value)}
                  style={{
                    fontSize: '12px',
                    padding: '6px 12px',
                    borderRadius: '8px',
                    fontFamily: 'Syne',
                    fontWeight: 500,
                    cursor: 'pointer',
                    transition: 'all 0.15s',
                    background: isSelected ? 'var(--amber-bg)' : 'var(--surface)',
                    border: isSelected ? '1px solid var(--amber-border)' : '1px solid var(--border)',
                    color: isSelected ? 'var(--amber)' : 'var(--text-2)',
                  }}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
          {duration === 'custom' && (
            <input
              type="date"
              value={customDate}
              onChange={e => setCustomDate(e.target.value)}
              min={new Date().toISOString().slice(0, 10)}
              style={{
                marginTop: '8px',
                width: '100%',
                fontSize: '13px',
                borderRadius: '8px',
                padding: '8px 12px',
                background: 'var(--input-bg)',
                border: '1px solid var(--border)',
                color: 'var(--text)',
                outline: 'none',
                boxSizing: 'border-box',
              }}
            />
          )}
        </div>

        <div>
          <label style={SL_LABEL}>
            Reason / Condition{' '}
            <span style={{ color: 'var(--text-3)', fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
              (optional)
            </span>
          </label>
          <textarea
            value={condition}
            onChange={e => setCondition(e.target.value)}
            placeholder="e.g. Wait for post-earnings price stabilization"
            rows={2}
            style={{
              marginTop: '6px',
              width: '100%',
              fontSize: '13px',
              borderRadius: '8px',
              padding: '8px 12px',
              background: 'var(--input-bg)',
              border: '1px solid var(--border)',
              color: 'var(--text)',
              outline: 'none',
              resize: 'none',
              boxSizing: 'border-box',
            }}
          />
        </div>

        <div className="flex justify-end gap-3 pt-1">
          <button
            onClick={onCancel}
            style={{
              fontSize: '13px',
              padding: '8px 16px',
              borderRadius: '8px',
              background: 'transparent',
              border: '1px solid var(--border)',
              color: 'var(--text-2)',
              cursor: 'pointer',
              fontFamily: 'Syne',
              transition: 'all 0.15s',
            }}
          >
            Cancel
          </button>
          <button
            onClick={() => onConfirm({ defer_until: deferUntil, defer_condition: condition })}
            disabled={duration === 'custom' && !customDate}
            style={{
              fontSize: '13px',
              padding: '8px 16px',
              borderRadius: '8px',
              background: 'var(--amber-bg)',
              border: '1px solid var(--amber-border)',
              color: 'var(--amber)',
              fontWeight: 700,
              cursor: 'pointer',
              fontFamily: 'Syne',
              opacity: duration === 'custom' && !customDate ? 0.5 : 1,
              transition: 'all 0.15s',
            }}
          >
            Defer
          </button>
        </div>
      </div>
    </div>
  );
}

export default function Orchestrator() {
  const [decisions, setDecisions] = useState([]);
  const [status, setStatus] = useState(null);
  const [running, setRunning] = useState(false);
  const [activeCategories, setActiveCategories] = useState(new Set(ALL_CATEGORIES));
  const [expandedId, setExpandedId] = useState(null);
  const [showHaltConfirm, setShowHaltConfirm] = useState(false);
  const [showResumeConfirm, setShowResumeConfirm] = useState(false);
  const [overrideTarget, setOverrideTarget] = useState(null); // {decision_id, action}
  const [deferTarget, setDeferTarget] = useState(null);       // {decision_id, ticker}
  const [actionPending, setActionPending] = useState(false);

  const loadDecisions = () => {
    getPMDecisions({ limit: 100 }).then(r => setDecisions(Array.isArray(r) ? r : [])).catch(() => {});
  };

  const loadStatus = () => {
    getPMStatus().then(r => setStatus(r)).catch(() => {});
  };

  useEffect(() => {
    loadDecisions();
    loadStatus();
    const id = setInterval(() => { loadStatus(); loadDecisions(); }, 30000);
    return () => clearInterval(id);
  }, []);

  const handleRunCycle = async () => {
    setRunning(true);
    try { await runPMCycle(); loadDecisions(); loadStatus(); } catch {}
    finally { setRunning(false); }
  };

  const handleHalt = async () => {
    setActionPending(true);
    setShowHaltConfirm(false);
    try { await haltPM(); loadStatus(); } catch {}
    finally { setActionPending(false); }
  };

  const handleResume = async () => {
    setActionPending(true);
    setShowResumeConfirm(false);
    try { await resumePM(); loadStatus(); } catch {}
    finally { setActionPending(false); }
  };

  const handleOverride = async (type) => {
    if (!overrideTarget) return;
    setActionPending(true);
    setOverrideTarget(null);
    try {
      await overrideDecision(overrideTarget.decision_id, {
        override_type: type,
        reason: `Human override via Dashboard — ${type}`,
      });
      loadDecisions();
    } catch {}
    finally { setActionPending(false); }
  };

  const handleDefer = async ({ defer_until, defer_condition }) => {
    if (!deferTarget) return;
    setActionPending(true);
    const target = deferTarget;
    setDeferTarget(null);
    try {
      await overrideDecision(target.decision_id, {
        override_type: 'DEFER',
        reason: defer_condition || 'Deferred by user via Dashboard',
        defer_until,
        defer_condition,
      });
      loadDecisions();
    } catch {}
    finally { setActionPending(false); }
  };

  const toggleCategory = (c) => {
    setActiveCategories(prev => {
      const next = new Set(prev);
      next.has(c) ? next.delete(c) : next.add(c);
      return next;
    });
  };

  const isHalted = status?.daily_loss_halt_triggered ?? false;
  const mode = status?.mode ?? 'autonomous';

  const filtered = decisions.filter(d => activeCategories.has(d.category));

  // Stats
  const executedCount = decisions.filter(d => d.execution_status === 'SENT_TO_EXECUTION').length;
  const deferredCount = decisions.filter(d => d.execution_status === 'DEFERRED').length;
  const rejectedCount = decisions.filter(d => d.decision === 'REJECT').length;
  const avgConf = decisions.length
    ? (decisions.reduce((s, d) => s + (d.confidence ?? 0), 0) / decisions.length).toFixed(2)
    : null;

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-7xl mx-auto">
      {/* Defer Modal */}
      {deferTarget && (
        <DeferModal
          target={deferTarget}
          onConfirm={handleDefer}
          onCancel={() => setDeferTarget(null)}
        />
      )}

      {/* Dialogs */}
      {showHaltConfirm && (
        <ConfirmDialog
          title="Halt new entries?"
          message="The AI PM will stop opening new positions. It will continue monitoring existing positions and processing exits. You can resume at any time."
          confirmLabel="Halt New Entries"
          destructive
          onConfirm={handleHalt}
          onCancel={() => setShowHaltConfirm(false)}
        />
      )}
      {showResumeConfirm && (
        <ConfirmDialog
          title="Resume normal operation?"
          message="The AI PM will resume opening new positions and running its full decision cycle."
          confirmLabel="Resume"
          onConfirm={handleResume}
          onCancel={() => setShowResumeConfirm(false)}
        />
      )}
      {overrideTarget && (
        <ConfirmDialog
          title={`${overrideTarget.action} decision ${overrideTarget.decision_id}?`}
          message={
            overrideTarget.action === 'BLOCK'
              ? 'This will prevent execution of the queued order.'
              : 'This will force-execute the decision regardless of PM status.'
          }
          confirmLabel={overrideTarget.action}
          destructive={overrideTarget.action === 'BLOCK'}
          onConfirm={() => handleOverride(overrideTarget.action)}
          onCancel={() => setOverrideTarget(null)}
        />
      )}

      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: 'var(--text)', fontFamily: 'Syne' }}>
            AI Portfolio Manager
          </h1>
          <p style={{ fontSize: '13px', color: 'var(--text-3)', marginTop: '2px' }}>
            Claude-powered decision engine — runs every 5 minutes during market hours
          </p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {isHalted ? (
            <button
              onClick={() => !actionPending && setShowResumeConfirm(true)}
              disabled={actionPending}
              style={{
                padding: '8px 16px',
                fontSize: '11px',
                fontWeight: 900,
                letterSpacing: '0.12em',
                borderRadius: '8px',
                background: 'var(--amber-bg)',
                border: '1px solid var(--amber-border)',
                color: 'var(--amber)',
                cursor: actionPending ? 'not-allowed' : 'pointer',
                fontFamily: 'Syne',
                transition: 'all 0.15s',
                opacity: actionPending ? 0.6 : 1,
              }}
            >
              HALTED — RESUME
            </button>
          ) : (
            <button
              onClick={() => !actionPending && setShowHaltConfirm(true)}
              disabled={actionPending}
              style={{
                padding: '8px 16px',
                fontSize: '11px',
                fontWeight: 900,
                letterSpacing: '0.12em',
                borderRadius: '8px',
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                color: 'var(--text-2)',
                cursor: actionPending ? 'not-allowed' : 'pointer',
                fontFamily: 'Syne',
                transition: 'all 0.15s',
                opacity: actionPending ? 0.6 : 1,
              }}
              onMouseEnter={e => {
                e.currentTarget.style.background = 'var(--red-bg)';
                e.currentTarget.style.borderColor = 'var(--red-border)';
                e.currentTarget.style.color = 'var(--red)';
              }}
              onMouseLeave={e => {
                e.currentTarget.style.background = 'var(--surface)';
                e.currentTarget.style.borderColor = 'var(--border)';
                e.currentTarget.style.color = 'var(--text-2)';
              }}
            >
              HALT NEW ENTRIES
            </button>
          )}
          <span
            style={{
              padding: '8px 12px',
              fontSize: '11px',
              fontWeight: 900,
              letterSpacing: '0.12em',
              borderRadius: '8px',
              fontFamily: 'Syne',
              ...(isHalted
                ? { background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-border)' }
                : mode === 'autonomous'
                ? { background: 'var(--accent)', color: '#000', border: '1px solid var(--accent)' }
                : { background: 'var(--surface)', color: 'var(--text-2)', border: '1px solid var(--border)' }),
            }}
          >
            {isHalted ? 'HALTED' : mode.toUpperCase()}
          </span>
          <button
            onClick={handleRunCycle}
            disabled={running}
            style={{
              padding: '8px 16px',
              fontSize: '13px',
              fontWeight: 600,
              borderRadius: '8px',
              background: 'var(--accent)',
              color: '#000',
              border: 'none',
              cursor: running ? 'not-allowed' : 'pointer',
              fontFamily: 'Syne',
              opacity: running ? 0.5 : 1,
              transition: 'opacity 0.15s',
            }}
          >
            {running ? 'Running…' : 'Run Cycle'}
          </button>
        </div>
      </div>

      {/* Halt Banner */}
      {isHalted && (
        <div
          className="rounded-xl px-4 py-3 flex items-center gap-2"
          style={{
            background: 'var(--amber-bg)',
            border: '1px solid var(--amber-border)',
            color: 'var(--amber)',
          }}
        >
          <span style={{ fontWeight: 700 }}>!</span>
          <span style={{ fontSize: '13px' }}>
            PM is <strong>halted</strong> — no new entries will be opened.
            {status?.halted_until && ` Auto-resumes: ${formatDateTime(status.halted_until)}.`}
            {' '}Existing positions are still being monitored.
          </span>
        </div>
      )}

      {/* Stats Strip */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          { label: 'Decisions',      value: decisions.length,                                      valueColor: 'var(--text)' },
          { label: 'Executed',       value: executedCount,                                          valueColor: 'var(--green)' },
          { label: 'Deferred',       value: deferredCount,                                          valueColor: 'var(--amber)' },
          { label: 'Rejected',       value: rejectedCount,                                          valueColor: rejectedCount > 0 ? 'var(--red)' : 'var(--text)' },
          { label: 'Avg Confidence', value: avgConf ? `${Math.round(avgConf * 100)}%` : '—',        valueColor: 'var(--blue)' },
        ].map(({ label, value, valueColor }) => (
          <div
            key={label}
            className="rounded-xl p-4"
            style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
          >
            <div style={SL_LABEL}>{label}</div>
            <div style={{ fontSize: '24px', fontWeight: 700, marginTop: '4px', color: valueColor, fontFamily: 'JetBrains Mono' }}>
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* Portfolio State */}
      {status?.portfolio && (
        <div
          className="rounded-xl px-5 py-4 flex flex-wrap gap-6"
          style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
        >
          {[
            { label: 'Gross Exposure', value: `${(status.portfolio.gross_exposure * 100).toFixed(1)}%` },
            { label: 'Net Exposure',   value: `${(status.portfolio.net_exposure * 100).toFixed(1)}%` },
            { label: 'Cash',           value: `${(status.portfolio.cash_pct * 100).toFixed(1)}%` },
            { label: 'Open Positions', value: status.portfolio.position_count },
          ].map(({ label, value }) => (
            <div key={label}>
              <div style={SL_LABEL}>{label}</div>
              <div style={{ fontSize: '18px', fontWeight: 700, color: 'var(--text)', marginTop: '2px', fontFamily: 'JetBrains Mono' }}>
                {value}
              </div>
            </div>
          ))}
          {status.active_critical_alerts > 0 && (
            <div>
              <div style={SL_LABEL}>Critical Alerts</div>
              <div style={{ fontSize: '18px', fontWeight: 700, color: 'var(--red)', marginTop: '2px', fontFamily: 'JetBrains Mono' }}>
                {status.active_critical_alerts}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Category Filter */}
      <div className="flex flex-wrap gap-2">
        <span style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-3)', alignSelf: 'center', marginRight: '4px' }}>
          Filter:
        </span>
        {ALL_CATEGORIES.map(c => {
          const on = activeCategories.has(c);
          const st = CATEGORY_BADGE_STYLE[c] || FALLBACK_BADGE;
          return (
            <button
              key={c}
              onClick={() => toggleCategory(c)}
              style={{
                fontSize: '11px',
                padding: '4px 10px',
                borderRadius: '9999px',
                fontWeight: 600,
                fontFamily: 'Syne',
                cursor: 'pointer',
                transition: 'opacity 0.15s',
                ...(on
                  ? { background: st.bg, color: st.color, border: `1px solid ${st.border}` }
                  : { background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--text-3)', opacity: 0.6 }),
              }}
            >
              {c}
            </button>
          );
        })}
      </div>

      {/* PM Decision Feed */}
      <div className="rounded-xl" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
        <div
          className="px-5 py-4 flex flex-wrap items-center justify-between gap-2"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          <p style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text)', fontFamily: 'Syne' }}>
            PM Decision Feed
          </p>
          <p style={{ ...SL_LABEL, letterSpacing: '0.08em' }}>{filtered.length} of {decisions.length} decisions</p>
        </div>
        <div>
          {filtered.length === 0 && (
            <div
              className="px-5 py-8 text-center"
              style={{ fontSize: '13px', color: 'var(--text-3)' }}
            >
              No decisions yet — run a PM cycle or wait for the next scheduled cycle
            </div>
          )}
          {filtered.map((d, idx) => {
            const isExpanded = expandedId === d.decision_id;
            const catSt = CATEGORY_BADGE_STYLE[d.category] || FALLBACK_BADGE;
            const decSt = DECISION_BADGE_STYLE[d.decision] || { bg: 'var(--surface-2)', color: 'var(--text-3)' };
            const execSt = EXEC_STATUS_STYLE[d.execution_status] || { bg: 'var(--surface-2)', color: 'var(--text-3)' };
            return (
              <div
                key={d.decision_id}
                className="px-5 py-4 transition-colors"
                style={{ borderTop: idx === 0 ? 'none' : '1px solid var(--border)' }}
                onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-2)'; }}
                onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
              >
                {/* Main row */}
                <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-3 md:gap-4">
                  <div className="flex flex-wrap items-start gap-2 sm:gap-3 flex-1 min-w-0">
                    {/* Category */}
                    <span
                      style={{
                        marginTop: '2px',
                        flexShrink: 0,
                        fontSize: '10px',
                        fontWeight: 700,
                        padding: '2px 8px',
                        borderRadius: '4px',
                        textTransform: 'uppercase',
                        letterSpacing: '0.08em',
                        fontFamily: 'Syne',
                        background: catSt.bg,
                        color: catSt.color,
                        border: `1px solid ${catSt.border}`,
                      }}
                    >
                      {d.category}
                    </span>
                    {/* Ticker */}
                    <span
                      style={{
                        marginTop: '2px',
                        flexShrink: 0,
                        fontFamily: 'JetBrains Mono',
                        fontSize: '13px',
                        fontWeight: 700,
                        color: 'var(--text)',
                        minWidth: '4rem',
                      }}
                    >
                      {d.ticker || <span style={{ color: 'var(--text-3)', fontWeight: 400, fontSize: '11px', fontFamily: 'Syne' }}>portfolio</span>}
                    </span>
                    {/* Decision badge */}
                    <span
                      style={{
                        marginTop: '2px',
                        flexShrink: 0,
                        fontSize: '11px',
                        fontWeight: 700,
                        padding: '2px 8px',
                        borderRadius: '4px',
                        fontFamily: 'Syne',
                        background: decSt.bg,
                        color: decSt.color,
                      }}
                    >
                      {d.decision}
                    </span>
                    {/* Reasoning */}
                    <p
                      style={{
                        fontSize: '12px',
                        color: 'var(--text-2)',
                        lineHeight: 1.6,
                        cursor: 'pointer',
                        flexBasis: '100%',
                        minWidth: 0,
                        ...(isExpanded ? {} : { overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }),
                      }}
                      className="md:flex-1 md:basis-auto"
                      onClick={() => setExpandedId(isExpanded ? null : d.decision_id)}
                    >
                      {d.reasoning || '—'}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2 sm:gap-4 w-full md:w-auto md:shrink-0">
                    <ConfidenceBar value={d.confidence} />
                    <span
                      style={{
                        fontSize: '10px',
                        lineHeight: 1.3,
                        fontWeight: 600,
                        padding: '2px 8px',
                        borderRadius: '4px',
                        textAlign: 'center',
                        maxWidth: '11rem',
                        fontFamily: 'Syne',
                        background: execSt.bg,
                        color: execSt.color,
                      }}
                    >
                      {d.execution_status}
                    </span>
                    <span style={{ fontSize: '11px', color: 'var(--text-3)', whiteSpace: 'nowrap', fontFamily: 'JetBrains Mono' }}>
                      {formatTime(d.timestamp)}
                    </span>
                  </div>
                </div>

                {/* Expanded details */}
                {isExpanded && (
                  <div
                    className="mt-3 space-y-2 pt-3"
                    style={{ borderTop: '1px solid var(--border)' }}
                  >
                    {d.risk_assessment && (
                      <div>
                        <span style={SL_LABEL}>Risk Assessment</span>
                        <p style={{ fontSize: '12px', color: 'var(--text-2)', marginTop: '2px' }}>{d.risk_assessment}</p>
                      </div>
                    )}
                    {d.action_details && Object.keys(d.action_details).length > 0 && (
                      <div>
                        <span style={SL_LABEL}>Action Details</span>
                        <pre
                          style={{
                            fontSize: '12px',
                            color: 'var(--text-2)',
                            marginTop: '2px',
                            fontFamily: 'JetBrains Mono',
                            background: 'var(--surface-2)',
                            borderRadius: '6px',
                            padding: '8px',
                            overflowX: 'auto',
                          }}
                        >
                          {JSON.stringify(d.action_details, null, 2)}
                        </pre>
                      </div>
                    )}
                    {d.context_snapshot && (
                      <div>
                        <span style={SL_LABEL}>Portfolio Context at Decision</span>
                        <div className="flex flex-wrap gap-4 mt-1">
                          {Object.entries(d.context_snapshot).map(([k, v]) => (
                            <div key={k}>
                              <span style={{ fontSize: '10px', color: 'var(--text-3)' }}>{k}</span>
                              <div style={{ fontSize: '12px', fontFamily: 'JetBrains Mono', color: 'var(--text-2)' }}>
                                {typeof v === 'number' && k.includes('exposure') ? `${(v * 100).toFixed(1)}%` : String(v)}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {d.human_override && (
                      <div
                        className="rounded p-2"
                        style={{ background: 'var(--blue-bg)', border: '1px solid var(--blue-border)' }}
                      >
                        <span
                          style={{
                            fontSize: '10px',
                            fontWeight: 700,
                            color: 'var(--blue)',
                            textTransform: 'uppercase',
                            letterSpacing: '0.08em',
                            fontFamily: 'Syne',
                          }}
                        >
                          Human Override: {d.human_override.override_type}
                        </span>
                        <p style={{ fontSize: '12px', color: 'var(--blue)', marginTop: '2px', opacity: 0.85 }}>
                          {d.human_override.reason}
                        </p>
                      </div>
                    )}
                    {/* Override buttons */}
                    {!d.human_override && d.execution_status === 'SENT_TO_EXECUTION' && (
                      <div className="flex flex-wrap gap-2 pt-1">
                        <button
                          onClick={() => setOverrideTarget({ decision_id: d.decision_id, action: 'BLOCK' })}
                          style={{
                            fontSize: '12px',
                            padding: '6px 12px',
                            borderRadius: '6px',
                            border: '1px solid var(--red-border)',
                            color: 'var(--red)',
                            background: 'transparent',
                            cursor: 'pointer',
                            fontFamily: 'Syne',
                            transition: 'background 0.15s',
                          }}
                          onMouseEnter={e => { e.currentTarget.style.background = 'var(--red-bg)'; }}
                          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                        >
                          Block
                        </button>
                      </div>
                    )}
                    {!d.human_override && d.execution_status === 'PENDING_HUMAN' && (
                      <div className="flex flex-wrap gap-2 pt-1">
                        <button
                          onClick={() => setOverrideTarget({ decision_id: d.decision_id, action: 'FORCE_EXECUTE' })}
                          style={{
                            fontSize: '12px',
                            padding: '6px 12px',
                            borderRadius: '6px',
                            border: '1px solid var(--green-border)',
                            color: 'var(--green)',
                            background: 'transparent',
                            cursor: 'pointer',
                            fontFamily: 'Syne',
                            transition: 'background 0.15s',
                          }}
                          onMouseEnter={e => { e.currentTarget.style.background = 'var(--green-bg)'; }}
                          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                        >
                          Force Execute
                        </button>
                        <button
                          onClick={() => setDeferTarget({ decision_id: d.decision_id, ticker: d.ticker })}
                          style={{
                            fontSize: '12px',
                            padding: '6px 12px',
                            borderRadius: '6px',
                            border: '1px solid var(--amber-border)',
                            color: 'var(--amber)',
                            background: 'transparent',
                            cursor: 'pointer',
                            fontFamily: 'Syne',
                            transition: 'background 0.15s',
                          }}
                          onMouseEnter={e => { e.currentTarget.style.background = 'var(--amber-bg)'; }}
                          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                        >
                          Defer
                        </button>
                        <button
                          onClick={() => setOverrideTarget({ decision_id: d.decision_id, action: 'BLOCK' })}
                          style={{
                            fontSize: '12px',
                            padding: '6px 12px',
                            borderRadius: '6px',
                            border: '1px solid var(--red-border)',
                            color: 'var(--red)',
                            background: 'transparent',
                            cursor: 'pointer',
                            fontFamily: 'Syne',
                            transition: 'background 0.15s',
                          }}
                          onMouseEnter={e => { e.currentTarget.style.background = 'var(--red-bg)'; }}
                          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                        >
                          Block
                        </button>
                      </div>
                    )}
                    {!d.human_override && d.execution_status === 'DEFERRED' && (
                      <div className="flex flex-wrap gap-2 pt-1">
                        <button
                          onClick={() => setOverrideTarget({ decision_id: d.decision_id, action: 'FORCE_EXECUTE' })}
                          style={{
                            fontSize: '12px',
                            padding: '6px 12px',
                            borderRadius: '6px',
                            border: '1px solid var(--green-border)',
                            color: 'var(--green)',
                            background: 'transparent',
                            cursor: 'pointer',
                            fontFamily: 'Syne',
                            transition: 'background 0.15s',
                          }}
                          onMouseEnter={e => { e.currentTarget.style.background = 'var(--green-bg)'; }}
                          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                        >
                          Force Execute
                        </button>
                        <button
                          onClick={() => setDeferTarget({ decision_id: d.decision_id, ticker: d.ticker })}
                          style={{
                            fontSize: '12px',
                            padding: '6px 12px',
                            borderRadius: '6px',
                            border: '1px solid var(--amber-border)',
                            color: 'var(--amber)',
                            background: 'transparent',
                            cursor: 'pointer',
                            fontFamily: 'Syne',
                            transition: 'background 0.15s',
                          }}
                          onMouseEnter={e => { e.currentTarget.style.background = 'var(--amber-bg)'; }}
                          onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                        >
                          Re-Defer
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
