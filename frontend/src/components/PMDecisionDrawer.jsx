import { useState } from 'react';
import { overrideDecision } from '../api/pm';
import { useAuth } from '../context/AuthContext';

const CATEGORY_STYLE = {
  NEW_ENTRY:    { bg: 'var(--green-bg)',     color: 'var(--green)',  border: 'var(--green-border)' },
  EXIT_TRIM:    { bg: 'var(--amber-bg)',     color: 'var(--amber)',  border: 'var(--amber-border)' },
  REBALANCE:    { bg: 'var(--blue-bg)',      color: 'var(--blue)',   border: 'var(--blue-border)' },
  CRISIS:       { bg: 'var(--red-bg)',       color: 'var(--red)',    border: 'var(--red-border)' },
  PRE_EARNINGS: { bg: 'var(--accent-muted)', color: 'var(--accent)', border: 'var(--accent-ring)' },
};

const EXEC_STYLE = {
  SENT_TO_EXECUTION:  { bg: 'var(--green-bg)',     color: 'var(--green)' },
  BLOCKED:            { bg: 'var(--red-bg)',        color: 'var(--red)' },
  DEFERRED:           { bg: 'var(--amber-bg)',      color: 'var(--amber)' },
  NO_ACTION:          { bg: 'var(--surface-2)',     color: 'var(--text-3)' },
  PENDING_HUMAN:      { bg: 'var(--blue-bg)',       color: 'var(--blue)' },
  TRIGGERED_PIPELINE: { bg: 'var(--accent-muted)',  color: 'var(--accent)' },
};

function SL({ children }) {
  return (
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne', marginBottom: '6px' }}>
      {children}
    </div>
  );
}

function Section({ label, children }) {
  return (
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: '12px', marginTop: '12px' }}>
      <SL>{label}</SL>
      {children}
    </div>
  );
}

function KVRow({ label, value, mono = false, color }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 0', borderBottom: '1px solid var(--border)' }}>
      <span style={{ fontSize: '11px', color: 'var(--text-3)' }}>{label}</span>
      <span style={{
        fontSize: '11px', fontWeight: 600,
        fontFamily: mono ? 'JetBrains Mono' : 'Syne',
        color: color ?? 'var(--text-2)',
      }}>
        {value ?? '—'}
      </span>
    </div>
  );
}

function ConfBar({ label, value }) {
  const pct = Math.round((value ?? 0) * 100);
  const color = pct >= 70 ? 'var(--green)' : pct >= 50 ? 'var(--amber)' : 'var(--red)';
  return (
    <div style={{ marginBottom: '8px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '3px' }}>
        <span style={{ fontSize: '10px', color: 'var(--text-3)', fontFamily: 'Syne' }}>{label}</span>
        <span style={{ fontSize: '10px', fontFamily: 'JetBrains Mono', fontWeight: 700, color }}>{pct}%</span>
      </div>
      <div style={{ height: '4px', borderRadius: '2px', background: 'var(--border)' }}>
        <div style={{ width: `${pct}%`, height: '100%', borderRadius: '2px', background: color }} />
      </div>
    </div>
  );
}

function fmtTime(ts) {
  if (!ts) return '—';
  return new Date(ts).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function fmtPct(v) {
  if (v == null) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

export default function PMDecisionDrawer({ decision: d, onClose, onOverrideApplied }) {
  const { isAdmin } = useAuth();
  const [execStatus, setExecStatus] = useState(d?.execution_status);
  const [loading, setLoading] = useState(null);

  if (!d) return null;

  const catSt  = CATEGORY_STYLE[d.category]  ?? { bg: 'var(--surface-2)', color: 'var(--text-3)', border: 'var(--border)' };
  const execSt = EXEC_STYLE[execStatus]        ?? { bg: 'var(--surface-2)', color: 'var(--text-3)' };
  const conf   = d.confidence != null ? Math.round(d.confidence * 100) : null;
  const confColor = conf >= 70 ? 'var(--green)' : conf >= 50 ? 'var(--amber)' : 'var(--red)';
  const ctx   = d.context_snapshot ?? {};
  const cb    = d.confidence_breakdown ?? {};
  const ad    = d.action_details ?? {};
  const hb    = d.hard_blocks_checked ?? {};
  const ho    = d.human_override;

  async function doOverride(type) {
    setLoading(type);
    try {
      await overrideDecision(d.decision_id, {
        override_type: type,
        reason: `Human override via Command Center — ${type}`,
      });
      const next = type === 'FORCE_EXECUTE' ? 'SENT_TO_EXECUTION' : type === 'BLOCK' ? 'BLOCKED' : 'DEFERRED';
      setExecStatus(next);
      onOverrideApplied?.();
    } catch {}
    setLoading(null);
  }

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 100, backdropFilter: 'blur(2px)' }}
      />

      {/* Drawer */}
      <div style={{
        position: 'fixed', top: 0, right: 0, bottom: 0, width: '500px', maxWidth: '95vw',
        background: 'var(--surface)', borderLeft: '1px solid var(--border)',
        zIndex: 101, overflowY: 'auto', display: 'flex', flexDirection: 'column',
      }}>

        {/* Header */}
        <div style={{
          padding: '16px 20px', borderBottom: '1px solid var(--border)',
          position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1,
        }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '10px' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '8px' }}>
              <span style={{
                fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
                padding: '3px 8px', borderRadius: '4px',
                background: catSt.bg, color: catSt.color, border: `1px solid ${catSt.border}`,
              }}>
                {d.category}
              </span>
              {d.ticker && (
                <span style={{ fontFamily: 'JetBrains Mono', fontSize: '20px', fontWeight: 800, color: 'var(--text)' }}>
                  {d.ticker}
                </span>
              )}
              <span style={{
                fontSize: '11px', fontWeight: 700, fontFamily: 'Syne',
                padding: '2px 8px', borderRadius: '4px',
                background: execSt.bg, color: execSt.color,
              }}>
                {d.decision}
              </span>
            </div>
            <button
              onClick={onClose}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)', padding: '2px', flexShrink: 0 }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '20px' }}>close</span>
            </button>
          </div>
          <div style={{ marginTop: '6px', display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{
              fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
              padding: '2px 7px', borderRadius: '3px',
              background: execSt.bg, color: execSt.color,
            }}>
              {execStatus?.replace(/_/g, ' ')}
            </span>
            <span style={{ fontSize: '10px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
              {fmtTime(d.timestamp)}
            </span>
            <span style={{ fontSize: '10px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
              {d.decision_id}
            </span>
          </div>
        </div>

        {/* Body */}
        <div style={{ padding: '16px 20px', flex: 1 }}>

          {/* Confidence */}
          <div style={{ marginBottom: '12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
              <SL style={{ margin: 0 }}>Confidence</SL>
              {conf != null && (
                <span style={{ fontFamily: 'JetBrains Mono', fontSize: '20px', fontWeight: 800, color: confColor }}>
                  {conf}%
                </span>
              )}
            </div>
            <div style={{ height: '5px', borderRadius: '3px', background: 'var(--border)', marginBottom: '8px' }}>
              <div style={{ width: `${conf ?? 0}%`, height: '100%', borderRadius: '3px', background: confColor }} />
            </div>
            {Object.keys(cb).length > 0 && (
              <div style={{ marginTop: '8px' }}>
                {Object.entries(cb).map(([k, v]) => (
                  <ConfBar key={k} label={k.replace(/_/g, ' ')} value={v} />
                ))}
              </div>
            )}
          </div>

          {/* Reasoning */}
          {d.reasoning && (
            <div style={{
              background: 'var(--surface-2)', border: '1px solid var(--border)',
              borderRadius: '8px', padding: '10px 12px', marginBottom: '12px',
            }}>
              <SL>Reasoning</SL>
              <p style={{ fontSize: '12px', color: 'var(--text)', margin: 0, lineHeight: 1.65 }}>
                {d.reasoning}
              </p>
            </div>
          )}

          {/* Risk assessment */}
          {d.risk_assessment && (
            <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', marginBottom: '12px' }}>
              <span className="material-symbols-outlined" style={{ fontSize: '14px', color: 'var(--amber)', marginTop: '1px', flexShrink: 0 }}>warning</span>
              <p style={{ fontSize: '12px', color: 'var(--text-2)', margin: 0, lineHeight: 1.55 }}>
                {d.risk_assessment}
              </p>
            </div>
          )}

          {/* Action details */}
          {Object.keys(ad).length > 0 && (
            <Section label="Action Details">
              {Object.entries(ad).map(([k, v]) => (
                <KVRow
                  key={k}
                  label={k.replace(/_/g, ' ')}
                  value={typeof v === 'number' ? (k.includes('pct') || k.includes('exposure') ? fmtPct(v) : v.toLocaleString()) : String(v)}
                  mono
                />
              ))}
            </Section>
          )}

          {/* Portfolio context at decision time */}
          <Section label="Portfolio Context">
            <KVRow label="Macro Regime"      value={ctx.macro_regime}                           />
            <KVRow label="Gross Exposure"    value={fmtPct(ctx.gross_exposure)}    mono          />
            <KVRow label="Net Exposure"      value={fmtPct(ctx.net_exposure)}      mono          />
            <KVRow label="Cash"              value={fmtPct(ctx.cash_pct)}          mono          />
            <KVRow label="Open Positions"    value={ctx.position_count}            mono          />
            <KVRow label="Critical Alerts"   value={ctx.active_critical_alerts}    mono
              color={ctx.active_critical_alerts > 0 ? 'var(--red)' : undefined}
            />
          </Section>

          {/* Hard blocks */}
          {Object.keys(hb).length > 0 && (
            <Section label="Hard Block Checks">
              {Object.entries(hb).map(([k, v]) => (
                <KVRow
                  key={k}
                  label={k.replace(/_/g, ' ')}
                  value={v === true ? '✓ Pass' : v === false ? '✗ Fail' : String(v)}
                  color={v === true ? 'var(--green)' : v === false ? 'var(--red)' : undefined}
                />
              ))}
            </Section>
          )}

          {/* Human override record */}
          {ho && (
            <Section label="Human Override">
              <KVRow label="Type"      value={ho.override_type?.replace(/_/g, ' ')} />
              <KVRow label="When"      value={fmtTime(ho.timestamp)} mono />
              {ho.reason && (
                <p style={{ fontSize: '11px', color: 'var(--text-2)', marginTop: '6px', lineHeight: 1.55, fontStyle: 'italic' }}>
                  "{ho.reason}"
                </p>
              )}
            </Section>
          )}
        </div>

        {/* Action bar — only for PENDING_HUMAN decisions */}
        {execStatus === 'PENDING_HUMAN' && isAdmin && (
          <div style={{
            padding: '12px 20px', borderTop: '1px solid var(--border)',
            background: 'var(--surface-2)', display: 'flex', gap: '8px',
            position: 'sticky', bottom: 0,
          }}>
            <button
              disabled={!!loading}
              onClick={() => doOverride('FORCE_EXECUTE')}
              style={{
                flex: 1, fontSize: '10px', fontWeight: 700, fontFamily: 'Syne',
                padding: '7px 0', borderRadius: '6px', border: '1px solid var(--green-border)',
                background: 'var(--green-bg)', color: 'var(--green)',
                cursor: 'pointer', opacity: loading && loading !== 'FORCE_EXECUTE' ? 0.5 : 1,
              }}
            >
              {loading === 'FORCE_EXECUTE' ? 'Approving…' : 'Approve'}
            </button>
            <button
              disabled={!!loading}
              onClick={() => doOverride('DEFER')}
              style={{
                padding: '7px 14px', fontSize: '10px', fontWeight: 700, fontFamily: 'Syne',
                borderRadius: '6px', border: '1px solid var(--amber-border)',
                background: 'var(--amber-bg)', color: 'var(--amber)',
                cursor: 'pointer', opacity: loading && loading !== 'DEFER' ? 0.5 : 1,
              }}
            >
              {loading === 'DEFER' ? 'Deferring…' : 'Defer'}
            </button>
            <button
              disabled={!!loading}
              onClick={() => doOverride('BLOCK')}
              style={{
                padding: '7px 14px', fontSize: '10px', fontWeight: 700, fontFamily: 'Syne',
                borderRadius: '6px', border: '1px solid var(--red-border)',
                background: 'var(--red-bg)', color: 'var(--red)',
                cursor: 'pointer', opacity: loading && loading !== 'BLOCK' ? 0.5 : 1,
              }}
            >
              {loading === 'BLOCK' ? 'Blocking…' : 'Block'}
            </button>
          </div>
        )}
      </div>
    </>
  );
}
