import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { runPMCycle } from '../api/pm';

const CATEGORY_STYLE = {
  NEW_ENTRY:    { bg: 'var(--green-bg)',     color: 'var(--green)',  border: 'var(--green-border)' },
  EXIT_TRIM:    { bg: 'var(--amber-bg)',     color: 'var(--amber)',  border: 'var(--amber-border)' },
  REBALANCE:    { bg: 'var(--blue-bg)',      color: 'var(--blue)',   border: 'var(--blue-border)' },
  CRISIS:       { bg: 'var(--red-bg)',       color: 'var(--red)',    border: 'var(--red-border)' },
  PRE_EARNINGS: { bg: 'var(--accent-muted)', color: 'var(--accent)', border: 'var(--accent-ring)' },
};

const EXEC_STYLE = {
  SENT_TO_EXECUTION:  { color: 'var(--green)' },
  BLOCKED:            { color: 'var(--red)' },
  DEFERRED:           { color: 'var(--amber)' },
  NO_ACTION:          { color: 'var(--text-3)' },
  PENDING_HUMAN:      { color: 'var(--blue)' },
  TRIGGERED_PIPELINE: { color: 'var(--accent)' },
};

function fmtAgo(ts) {
  if (!ts) return '—';
  const diff = (Date.now() - new Date(ts)) / 1000;
  if (diff < 60)    return 'just now';
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function fmtHaltedUntil(ts) {
  if (!ts) return null;
  const d = new Date(ts);
  if (isNaN(d)) return null;
  if (d < new Date()) return null;
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

export default function OrchestratorBrain({ status, onRefresh }) {
  const { isAdmin } = useAuth();
  const [running, setRunning] = useState(false);

  const isHalted     = status?.daily_loss_halt_triggered ?? false;
  const mode         = status?.mode ?? 'supervised';
  const lastCycle    = status?.last_cycle;
  const lastTs       = lastCycle?.timestamp ?? null;
  const decisionsToday   = status?.decisions_today ?? 0;
  const criticalAlerts   = status?.active_critical_alerts ?? 0;
  const haltedUntil      = fmtHaltedUntil(status?.halted_until);
  const cycleIntervalMin = status?.cycle_interval_seconds
    ? Math.round(status.cycle_interval_seconds / 60)
    : 5;

  // Use real pm_cycle_status from DB; local `running` covers the brief window between
  // button click and the next poll reflecting RUNNING in pm_config.
  const dbCycleStatus = status?.pm_cycle_status ?? 'IDLE';
  const isActuallyRunning = running || dbCycleStatus === 'RUNNING' || (status?.pm_is_running ?? false);

  const stateKey = isHalted ? 'HALTED' : isActuallyRunning ? 'RUNNING' : dbCycleStatus === 'FAILED' ? 'FAILED' : 'IDLE';
  const STATE_CONFIG = {
    IDLE:    { label: 'IDLE',    color: 'var(--text-2)',  dotStyle: { background: 'var(--text-3)' } },
    RUNNING: { label: 'RUNNING', color: 'var(--accent)',  dotStyle: { background: 'var(--accent)' }, pulse: true },
    HALTED:  { label: 'HALTED',  color: 'var(--amber)',   dotStyle: { background: 'var(--amber)' } },
    FAILED:  { label: 'FAILED',  color: 'var(--red)',     dotStyle: { background: 'var(--red)' } },
  };
  const cfg = STATE_CONFIG[stateKey];

  const stats = status?.portfolio;

  const handleRun = async () => {
    setRunning(true);
    try {
      await runPMCycle();
      onRefresh?.();
    } catch {}
    finally { setRunning(false); }
  };

  return (
    <div
      className="rounded-xl p-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      {/* Header */}
      <div className="flex items-start gap-2 mb-3">
        <div
          className={`w-2 h-2 rounded-full flex-shrink-0 mt-1 ${cfg.pulse ? 'pulse-brain' : ''}`}
          style={cfg.dotStyle}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
            AI Orchestrator
          </div>
          <div style={{ fontFamily: 'Syne', fontSize: '11px', fontWeight: 800, color: cfg.color, lineHeight: 1.2 }}>
            {cfg.label} · {mode.toUpperCase()}
          </div>
          {haltedUntil && (
            <div style={{ fontSize: '9px', color: 'var(--amber)', fontFamily: 'JetBrains Mono', marginTop: '1px' }}>
              halted until {haltedUntil}
            </div>
          )}
        </div>
        <div className="text-right flex-shrink-0">
          <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>last cycle</div>
          <div style={{ fontSize: '11px', color: 'var(--text-2)', fontFamily: 'JetBrains Mono', fontWeight: 600 }}>
            {fmtAgo(lastTs)}
          </div>
        </div>
      </div>

      {/* Stats row: decisions today + critical alerts + cycle interval */}
      <div className="flex items-center gap-3 mb-3" style={{ borderBottom: '1px solid var(--border)', paddingBottom: '10px' }}>
        <div>
          <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700, textTransform: 'uppercase' }}>Today</div>
          <div style={{ fontFamily: 'JetBrains Mono', fontSize: '15px', fontWeight: 700, color: 'var(--text)' }}>{decisionsToday}</div>
        </div>
        <div style={{ width: '1px', alignSelf: 'stretch', background: 'var(--border)' }} />
        <div>
          <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700, textTransform: 'uppercase' }}>Cycle</div>
          <div style={{ fontFamily: 'JetBrains Mono', fontSize: '15px', fontWeight: 700, color: 'var(--text)' }}>{cycleIntervalMin}m</div>
        </div>
        {criticalAlerts > 0 && (
          <>
            <div style={{ width: '1px', alignSelf: 'stretch', background: 'var(--border)' }} />
            <div>
              <div style={{ fontSize: '9px', color: 'var(--red)', fontFamily: 'Syne', fontWeight: 700, textTransform: 'uppercase' }}>Critical</div>
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: '15px', fontWeight: 700, color: 'var(--red)' }}>{criticalAlerts}</div>
            </div>
          </>
        )}
      </div>

      {/* Portfolio stats */}
      {stats && (
        <div className="grid grid-cols-4 gap-2 mb-3">
          {[
            { label: 'Gross',  value: `${(stats.gross_exposure * 100).toFixed(0)}%` },
            { label: 'Net',    value: `${(stats.net_exposure   * 100).toFixed(0)}%` },
            { label: 'Cash',   value: `${(stats.cash_pct       * 100).toFixed(0)}%` },
            { label: 'Pos',    value: stats.position_count },
          ].map(({ label, value }) => (
            <div key={label} className="rounded-lg p-2 text-center" style={{ background: 'var(--surface-2)' }}>
              <div style={{ fontSize: '8px', color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em' }}>
                {label}
              </div>
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', fontWeight: 700, color: 'var(--text)', marginTop: '1px' }}>
                {value}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Last decision row */}
      {lastCycle && (
        <div
          className="rounded-lg px-3 py-2 mb-3 flex items-center gap-2 flex-wrap"
          style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}
        >
          <div style={{ fontSize: '8px', color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700, textTransform: 'uppercase', marginRight: '2px' }}>
            Last
          </div>
          {lastCycle.category && (() => {
            const cs = CATEGORY_STYLE[lastCycle.category] ?? { bg: 'var(--surface-2)', color: 'var(--text-3)', border: 'var(--border)' };
            return (
              <span style={{
                fontSize: '8px', fontWeight: 700, fontFamily: 'Syne',
                padding: '1px 5px', borderRadius: '3px',
                background: cs.bg, color: cs.color, border: `1px solid ${cs.border}`,
              }}>
                {lastCycle.category}
              </span>
            );
          })()}
          {lastCycle.decision && (
            <span style={{
              fontFamily: 'Syne', fontSize: '10px', fontWeight: 700,
              color: EXEC_STYLE[lastCycle.execution_status]?.color ?? 'var(--text-2)',
            }}>
              {lastCycle.decision}
            </span>
          )}
          {lastCycle.execution_status && (
            <span style={{ marginLeft: 'auto', fontSize: '8px', color: EXEC_STYLE[lastCycle.execution_status]?.color ?? 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700 }}>
              {lastCycle.execution_status.replace(/_/g, ' ')}
            </span>
          )}
        </div>
      )}

      {/* Run cycle button */}
      <button
        onClick={handleRun}
        disabled={running || !isAdmin}
        title={!isAdmin ? 'Guest mode — read only' : undefined}
        className="w-full py-2 rounded-lg text-[11px] font-bold tracking-wide transition-all"
        style={{
          fontFamily: 'Syne',
          background: running ? 'var(--surface-2)' : 'var(--accent)',
          color: running ? 'var(--text-2)' : '#000',
          border: 'none',
          cursor: running || !isAdmin ? 'not-allowed' : 'pointer',
          opacity: !isAdmin ? 0.5 : 1,
        }}
      >
        {running ? 'Running cycle…' : 'Run PM Cycle'}
      </button>
    </div>
  );
}
