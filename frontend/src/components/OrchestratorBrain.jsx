import { useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { runPMCycle } from '../api/pm';

function fmtAgo(ts) {
  if (!ts) return '—';
  const diff = (Date.now() - new Date(ts)) / 1000;
  if (diff < 60)    return 'just now';
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

const STATE_CONFIG = {
  IDLE:    { label: 'IDLE',    color: 'var(--text-2)',  dotClass: 'dot-gray' },
  RUNNING: { label: 'RUNNING', color: 'var(--accent)',  dotClass: 'pulse-brain', pulse: true },
  BLOCKED: { label: 'HALTED', color: 'var(--amber)',  dotClass: 'dot-amber' },
  HALTED:  { label: 'HALTED', color: 'var(--amber)',  dotClass: 'dot-amber' },
};

export default function OrchestratorBrain({ status, onRefresh }) {
  const { isAdmin } = useAuth();
  const [running, setRunning] = useState(false);

  const isHalted = status?.daily_loss_halt_triggered ?? false;
  const mode     = status?.mode ?? 'supervised';
  const lastRun  = status?.last_cycle ?? status?.updated_at ?? null;

  const stateKey = isHalted ? 'HALTED' : running ? 'RUNNING' : 'IDLE';
  const cfg = STATE_CONFIG[stateKey] ?? STATE_CONFIG.IDLE;

  const handleRun = async () => {
    setRunning(true);
    try {
      await runPMCycle();
      onRefresh?.();
    } catch {}
    finally { setRunning(false); }
  };

  const stats = status?.portfolio;

  return (
    <div
      className="rounded-xl p-4"
      style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
    >
      {/* Header row */}
      <div className="flex items-center gap-2 mb-3">
        <div
          className={`w-2 h-2 rounded-full flex-shrink-0 ${cfg.pulse ? 'pulse-brain' : cfg.dotClass}`}
          style={{ background: cfg.color }}
        />
        <div>
          <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
            AI Orchestrator
          </div>
          <div style={{ fontFamily: 'Syne', fontSize: '11px', fontWeight: 800, color: cfg.color, lineHeight: 1.2 }}>
            {cfg.label} · {mode.toUpperCase()}
          </div>
        </div>
        <div className="ml-auto text-right">
          <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>Last cycle</div>
          <div style={{ fontSize: '11px', color: 'var(--text-2)', fontFamily: 'JetBrains Mono' }}>
            {fmtAgo(lastRun)}
          </div>
        </div>
      </div>

      {/* Portfolio context */}
      {stats && (
        <div className="grid grid-cols-3 gap-2 mb-3">
          {[
            { label: 'Gross',    value: `${(stats.gross_exposure * 100).toFixed(0)}%` },
            { label: 'Net',      value: `${(stats.net_exposure * 100).toFixed(0)}%`   },
            { label: 'Positions',value: stats.position_count },
          ].map(({ label, value }) => (
            <div key={label} className="rounded-lg p-2 text-center" style={{ background: 'var(--surface-2)' }}>
              <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em' }}>{label}</div>
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: '13px', fontWeight: 700, color: 'var(--text)', marginTop: '1px' }}>{value}</div>
            </div>
          ))}
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
