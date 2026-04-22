import { useEffect, useState } from 'react';
import { getPMStatus, getPMDecisions, overrideDecision, haltPM, resumePM, runPMCycle } from '../api/pm';
import ConfirmDialog from '../components/ConfirmDialog';

const CATEGORY_BADGE = {
  NEW_ENTRY:    'bg-green-100 text-green-700',
  EXIT_TRIM:    'bg-orange-100 text-orange-700',
  REBALANCE:    'bg-blue-100 text-blue-700',
  CRISIS:       'bg-red-100 text-red-700',
  PRE_EARNINGS: 'bg-purple-100 text-purple-700',
};

const DECISION_BADGE = {
  EXECUTE:            'bg-green-600 text-white',
  MODIFY_SIZE:        'bg-green-100 text-green-700',
  HOLD:               'bg-gray-100 text-gray-600',
  NO_ACTION:          'bg-gray-100 text-gray-600',
  DEFER:              'bg-yellow-100 text-yellow-700',
  REJECT:             'bg-red-100 text-red-700',
  WATCHLIST:          'bg-purple-100 text-purple-700',
  TRIM:               'bg-orange-100 text-orange-700',
  CLOSE:              'bg-red-600 text-white',
  ADD:                'bg-green-100 text-green-700',
  REDUCE_EXPOSURE:    'bg-orange-100 text-orange-700',
  HALT_NEW_ENTRIES:   'bg-red-100 text-red-700',
  LIQUIDATE_TO_TARGET:'bg-red-600 text-white',
  MONITOR:            'bg-yellow-100 text-yellow-700',
  REBALANCE:          'bg-blue-100 text-blue-700',
  RAISE_CASH:         'bg-orange-100 text-orange-700',
  DEPLOY_CASH:        'bg-green-100 text-green-700',
  SIZE_UP:            'bg-green-600 text-white',
  EXIT:               'bg-red-600 text-white',
};

const EXEC_STATUS_BADGE = {
  SENT_TO_EXECUTION: 'bg-green-100 text-green-700',
  BLOCKED:           'bg-red-100 text-red-700',
  DEFERRED:          'bg-yellow-100 text-yellow-700',
  NO_ACTION:         'bg-gray-100 text-gray-500',
  PENDING_HUMAN:     'bg-blue-100 text-blue-700',
};

const ALL_CATEGORIES = ['NEW_ENTRY', 'EXIT_TRIM', 'REBALANCE', 'CRISIS', 'PRE_EARNINGS'];

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
  if (value == null) return <span className="text-gray-400">—</span>;
  const pct = Math.round(value * 100);
  const color = pct >= 70 ? 'bg-green-500' : pct >= 50 ? 'bg-yellow-400' : 'bg-red-400';
  return (
    <div className="flex items-center gap-2">
      <div className="w-16 h-1.5 bg-gray-200 rounded-full overflow-hidden">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-600 font-mono">{pct}%</span>
    </div>
  );
}

const DEFER_OPTIONS = [
  { label: '1 Day',    value: '1' },
  { label: '3 Days',   value: '3' },
  { label: '1 Week',   value: '7' },
  { label: 'Custom…',  value: 'custom' },
];

function DeferModal({ target, onConfirm, onCancel }) {
  const [duration, setDuration] = useState('3');
  const [customDate, setCustomDate] = useState('');
  const [condition, setCondition] = useState('');

  const deferUntil = duration === 'custom' ? customDate : duration;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6 space-y-4">
        <h2 className="text-base font-bold text-gray-900">Defer Decision</h2>
        <p className="text-sm text-gray-500">
          Set when to re-evaluate <span className="font-mono font-semibold">{target.ticker || 'this decision'}</span>.
          The research scheduler will re-queue it automatically on that date.
        </p>

        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Re-check in</label>
          <div className="flex gap-2 mt-1.5 flex-wrap">
            {DEFER_OPTIONS.map(opt => (
              <button
                key={opt.value}
                onClick={() => setDuration(opt.value)}
                className={`text-xs px-3 py-1.5 rounded-lg border font-medium transition-colors ${
                  duration === opt.value
                    ? 'bg-yellow-500 text-white border-yellow-500'
                    : 'bg-white text-gray-600 border-gray-200 hover:border-yellow-300'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          {duration === 'custom' && (
            <input
              type="date"
              value={customDate}
              onChange={e => setCustomDate(e.target.value)}
              min={new Date().toISOString().slice(0, 10)}
              className="mt-2 w-full text-sm border border-gray-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-yellow-400"
            />
          )}
        </div>

        <div>
          <label className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
            Reason / Condition <span className="text-gray-400 font-normal">(optional)</span>
          </label>
          <textarea
            value={condition}
            onChange={e => setCondition(e.target.value)}
            placeholder="e.g. Wait for post-earnings price stabilization"
            rows={2}
            className="mt-1.5 w-full text-sm border border-gray-200 rounded-lg px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-yellow-400"
          />
        </div>

        <div className="flex justify-end gap-3 pt-1">
          <button
            onClick={onCancel}
            className="text-sm px-4 py-2 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => onConfirm({ defer_until: deferUntil, defer_condition: condition })}
            disabled={duration === 'custom' && !customDate}
            className="text-sm px-4 py-2 rounded-lg bg-yellow-500 text-white font-semibold hover:bg-yellow-600 disabled:opacity-50 transition-colors"
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
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
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
          <h1 className="text-xl font-bold text-gray-900">AI Portfolio Manager</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Claude-powered decision engine — runs every 5 minutes during market hours
          </p>
        </div>
        <div className="flex items-center gap-3 flex-wrap">
          {isHalted ? (
            <button
              onClick={() => !actionPending && setShowResumeConfirm(true)}
              disabled={actionPending}
              className="px-4 py-2 text-xs font-black tracking-widest rounded-lg border bg-yellow-50 text-yellow-700 border-yellow-300 hover:bg-yellow-100 transition-colors"
            >
              HALTED — RESUME
            </button>
          ) : (
            <button
              onClick={() => !actionPending && setShowHaltConfirm(true)}
              disabled={actionPending}
              className="px-4 py-2 text-xs font-black tracking-widest rounded-lg border bg-white text-gray-700 border-gray-300 hover:bg-red-50 hover:border-red-300 hover:text-red-700 transition-colors"
            >
              HALT NEW ENTRIES
            </button>
          )}
          <span className={`px-3 py-2 text-xs font-black tracking-widest rounded-lg border ${
            isHalted
              ? 'bg-yellow-50 text-yellow-700 border-yellow-300'
              : mode === 'autonomous'
              ? 'bg-blue-600 text-white border-blue-600'
              : 'bg-white text-gray-700 border-gray-300'
          }`}>
            {isHalted ? 'HALTED' : mode.toUpperCase()}
          </span>
          <button
            onClick={handleRunCycle}
            disabled={running}
            className="px-4 py-2 text-sm font-semibold bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {running ? 'Running…' : 'Run Cycle'}
          </button>
        </div>
      </div>

      {/* Halt Banner */}
      {isHalted && (
        <div className="rounded-xl bg-yellow-50 border border-yellow-200 px-4 py-3 flex items-center gap-2">
          <span className="text-yellow-500 font-bold">!</span>
          <span className="text-sm text-yellow-700">
            PM is <strong>halted</strong> — no new entries will be opened.
            {status?.halted_until && ` Auto-resumes: ${formatDateTime(status.halted_until)}.`}
            {' '}Existing positions are still being monitored.
          </span>
        </div>
      )}

      {/* Stats Strip */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {[
          { label: 'Decisions', value: decisions.length, color: 'text-gray-900' },
          { label: 'Executed', value: executedCount, color: 'text-green-600' },
          { label: 'Deferred', value: deferredCount, color: 'text-yellow-600' },
          { label: 'Rejected', value: rejectedCount, color: rejectedCount > 0 ? 'text-red-600' : 'text-gray-900' },
          { label: 'Avg Confidence', value: avgConf ? `${Math.round(avgConf * 100)}%` : '—', color: 'text-blue-600' },
        ].map(({ label, value, color }) => (
          <div key={label} className="bg-white rounded-xl border border-gray-200 p-4">
            <div className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</div>
            <div className={`text-2xl font-bold mt-1 ${color}`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Portfolio State */}
      {status?.portfolio && (
        <div className="bg-white rounded-xl border border-gray-200 px-5 py-4 flex flex-wrap gap-6">
          <div>
            <span className="text-xs text-gray-500 uppercase tracking-wide">Gross Exposure</span>
            <div className="text-lg font-bold text-gray-900 mt-0.5">
              {(status.portfolio.gross_exposure * 100).toFixed(1)}%
            </div>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase tracking-wide">Net Exposure</span>
            <div className="text-lg font-bold text-gray-900 mt-0.5">
              {(status.portfolio.net_exposure * 100).toFixed(1)}%
            </div>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase tracking-wide">Cash</span>
            <div className="text-lg font-bold text-gray-900 mt-0.5">
              {(status.portfolio.cash_pct * 100).toFixed(1)}%
            </div>
          </div>
          <div>
            <span className="text-xs text-gray-500 uppercase tracking-wide">Open Positions</span>
            <div className="text-lg font-bold text-gray-900 mt-0.5">
              {status.portfolio.position_count}
            </div>
          </div>
          {status.active_critical_alerts > 0 && (
            <div>
              <span className="text-xs text-gray-500 uppercase tracking-wide">Critical Alerts</span>
              <div className="text-lg font-bold text-red-600 mt-0.5">
                {status.active_critical_alerts}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Category Filter */}
      <div className="flex flex-wrap gap-2">
        <span className="text-xs font-medium text-gray-500 self-center mr-1">Filter:</span>
        {ALL_CATEGORIES.map(c => {
          const on = activeCategories.has(c);
          const badge = CATEGORY_BADGE[c] || 'bg-gray-100 text-gray-600';
          return (
            <button
              key={c}
              onClick={() => toggleCategory(c)}
              className={`text-xs px-2.5 py-1 rounded-full font-medium border transition-opacity ${
                on ? badge + ' border-transparent' : 'bg-white text-gray-400 border-gray-200 opacity-50'
              }`}
            >
              {c}
            </button>
          );
        })}
      </div>

      {/* PM Decision Feed */}
      <div className="bg-white rounded-xl border border-gray-200">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
          <p className="text-sm font-semibold text-gray-900">PM Decision Feed</p>
          <p className="text-xs text-gray-400">{filtered.length} of {decisions.length} decisions</p>
        </div>
        <div className="divide-y divide-gray-100">
          {filtered.length === 0 && (
            <div className="px-5 py-8 text-sm text-gray-400 text-center">
              No decisions yet — run a PM cycle or wait for the next scheduled cycle
            </div>
          )}
          {filtered.map(d => {
            const isExpanded = expandedId === d.decision_id;
            return (
              <div key={d.decision_id} className="px-5 py-4 hover:bg-gray-50 transition-colors">
                {/* Main row */}
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3 flex-1 min-w-0">
                    {/* Category */}
                    <span className={`mt-0.5 shrink-0 text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wide ${CATEGORY_BADGE[d.category] || 'bg-gray-100 text-gray-600'}`}>
                      {d.category}
                    </span>
                    {/* Ticker */}
                    <span className="mt-0.5 shrink-0 font-mono text-sm font-bold text-gray-800 w-16">
                      {d.ticker || <span className="text-gray-400 font-normal text-xs">portfolio</span>}
                    </span>
                    {/* Decision */}
                    <span className={`mt-0.5 shrink-0 text-xs font-bold px-2 py-0.5 rounded ${DECISION_BADGE[d.decision] || 'bg-gray-100 text-gray-600'}`}>
                      {d.decision}
                    </span>
                    {/* Reasoning */}
                    <p
                      className={`text-xs text-gray-600 leading-relaxed flex-1 min-w-0 cursor-pointer ${isExpanded ? '' : 'line-clamp-2'}`}
                      onClick={() => setExpandedId(isExpanded ? null : d.decision_id)}
                    >
                      {d.reasoning || '—'}
                    </p>
                  </div>
                  <div className="flex items-center gap-4 shrink-0">
                    <ConfidenceBar value={d.confidence} />
                    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded ${EXEC_STATUS_BADGE[d.execution_status] || 'bg-gray-100 text-gray-500'}`}>
                      {d.execution_status}
                    </span>
                    <span className="text-xs text-gray-400 whitespace-nowrap">{formatTime(d.timestamp)}</span>
                  </div>
                </div>

                {/* Expanded details */}
                {isExpanded && (
                  <div className="mt-3 ml-0 space-y-2 border-t border-gray-100 pt-3">
                    {d.risk_assessment && (
                      <div>
                        <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Risk Assessment</span>
                        <p className="text-xs text-gray-600 mt-0.5">{d.risk_assessment}</p>
                      </div>
                    )}
                    {d.action_details && Object.keys(d.action_details).length > 0 && (
                      <div>
                        <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Action Details</span>
                        <pre className="text-xs text-gray-600 mt-0.5 font-mono bg-gray-50 rounded p-2 overflow-x-auto">
                          {JSON.stringify(d.action_details, null, 2)}
                        </pre>
                      </div>
                    )}
                    {d.context_snapshot && (
                      <div>
                        <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide">Portfolio Context at Decision</span>
                        <div className="flex flex-wrap gap-4 mt-1">
                          {Object.entries(d.context_snapshot).map(([k, v]) => (
                            <div key={k}>
                              <span className="text-[10px] text-gray-400">{k}</span>
                              <div className="text-xs font-mono text-gray-700">
                                {typeof v === 'number' && k.includes('exposure') ? `${(v * 100).toFixed(1)}%` : String(v)}
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {d.human_override && (
                      <div className="bg-blue-50 rounded p-2">
                        <span className="text-[10px] font-semibold text-blue-600 uppercase">Human Override: {d.human_override.override_type}</span>
                        <p className="text-xs text-blue-700 mt-0.5">{d.human_override.reason}</p>
                      </div>
                    )}
                    {/* Override buttons */}
                    {!d.human_override && d.execution_status === 'SENT_TO_EXECUTION' && (
                      <div className="flex gap-2 pt-1">
                        <button
                          onClick={() => setOverrideTarget({ decision_id: d.decision_id, action: 'BLOCK' })}
                          className="text-xs px-3 py-1.5 rounded border border-red-200 text-red-600 hover:bg-red-50 transition-colors"
                        >
                          Block
                        </button>
                      </div>
                    )}
                    {!d.human_override && d.execution_status === 'PENDING_HUMAN' && (
                      <div className="flex gap-2 pt-1">
                        <button
                          onClick={() => setOverrideTarget({ decision_id: d.decision_id, action: 'FORCE_EXECUTE' })}
                          className="text-xs px-3 py-1.5 rounded border border-green-200 text-green-700 hover:bg-green-50 transition-colors"
                        >
                          Force Execute
                        </button>
                        <button
                          onClick={() => setDeferTarget({ decision_id: d.decision_id, ticker: d.ticker })}
                          className="text-xs px-3 py-1.5 rounded border border-yellow-300 text-yellow-700 hover:bg-yellow-50 transition-colors"
                        >
                          Defer
                        </button>
                        <button
                          onClick={() => setOverrideTarget({ decision_id: d.decision_id, action: 'BLOCK' })}
                          className="text-xs px-3 py-1.5 rounded border border-red-200 text-red-600 hover:bg-red-50 transition-colors"
                        >
                          Block
                        </button>
                      </div>
                    )}
                    {!d.human_override && d.execution_status === 'DEFERRED' && (
                      <div className="flex gap-2 pt-1">
                        <button
                          onClick={() => setOverrideTarget({ decision_id: d.decision_id, action: 'FORCE_EXECUTE' })}
                          className="text-xs px-3 py-1.5 rounded border border-green-200 text-green-700 hover:bg-green-50 transition-colors"
                        >
                          Force Execute
                        </button>
                        <button
                          onClick={() => setDeferTarget({ decision_id: d.decision_id, ticker: d.ticker })}
                          className="text-xs px-3 py-1.5 rounded border border-yellow-300 text-yellow-700 hover:bg-yellow-50 transition-colors"
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
