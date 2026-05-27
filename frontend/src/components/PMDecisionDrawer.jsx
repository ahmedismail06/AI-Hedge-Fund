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
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne', marginBottom: '8px' }}>
      {children}
    </div>
  );
}

function Section({ label, children }) {
  return (
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: '14px', marginTop: '14px' }}>
      <SL>{label}</SL>
      {children}
    </div>
  );
}

function KVRow({ label, value, mono = false, color }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
      <span style={{ fontSize: '12px', color: 'var(--text-3)' }}>{label}</span>
      <span style={{ fontSize: '12px', fontWeight: 600, fontFamily: mono ? 'JetBrains Mono' : 'Syne', color: color ?? 'var(--text-2)' }}>
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
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
        <span style={{ fontSize: '11px', color: 'var(--text-3)', fontFamily: 'Syne' }}>{label}</span>
        <span style={{ fontSize: '11px', fontFamily: 'JetBrains Mono', fontWeight: 700, color }}>{pct}%</span>
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
        style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 100, backdropFilter: 'blur(3px)' }}
      />

      {/* Centered modal */}
      <div style={{
        position: 'fixed', inset: 0, zIndex: 101,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: '24px',
        pointerEvents: 'none',
      }}>
        <div style={{
          width: '100%', maxWidth: '680px', maxHeight: '88vh',
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: '16px', display: 'flex', flexDirection: 'column',
          pointerEvents: 'auto', overflow: 'hidden',
          boxShadow: '0 24px 64px rgba(0,0,0,0.4)',
        }}>

          {/* Header */}
          <div style={{
            padding: '20px 24px 16px', borderBottom: '1px solid var(--border)',
            background: 'var(--surface)', flexShrink: 0,
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '10px' }}>
                <span style={{
                  fontSize: '10px', fontWeight: 700, fontFamily: 'Syne',
                  padding: '3px 9px', borderRadius: '5px',
                  background: catSt.bg, color: catSt.color, border: `1px solid ${catSt.border}`,
                }}>
                  {d.category}
                </span>
                {d.ticker && (
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: '24px', fontWeight: 800, color: 'var(--text)' }}>
                    {d.ticker}
                  </span>
                )}
                <span style={{
                  fontSize: '13px', fontWeight: 700, fontFamily: 'Syne',
                  padding: '3px 10px', borderRadius: '5px',
                  background: execSt.bg, color: execSt.color,
                }}>
                  {d.decision}
                </span>
              </div>
              <button
                onClick={onClose}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)', padding: '4px', flexShrink: 0, lineHeight: 1 }}
              >
                <span className="material-symbols-outlined" style={{ fontSize: '22px' }}>close</span>
              </button>
            </div>
            <div style={{ marginTop: '8px', display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{
                fontSize: '10px', fontWeight: 700, fontFamily: 'Syne',
                padding: '2px 8px', borderRadius: '4px',
                background: execSt.bg, color: execSt.color,
              }}>
                {execStatus?.replace(/_/g, ' ')}
              </span>
              <span style={{ fontSize: '11px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
                {fmtTime(d.timestamp)}
              </span>
              <span style={{ fontSize: '10px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono', opacity: 0.6 }}>
                {d.decision_id}
              </span>
            </div>
          </div>

          {/* Scrollable body */}
          <div style={{ padding: '20px 24px', flex: 1, overflowY: 'auto' }} className="term-scroll">

            {/* Confidence */}
            <div style={{ marginBottom: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px' }}>
                <SL>Confidence</SL>
                {conf != null && (
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: '24px', fontWeight: 800, color: confColor, marginBottom: '8px' }}>
                    {conf}%
                  </span>
                )}
              </div>
              <div style={{ height: '6px', borderRadius: '3px', background: 'var(--border)', marginBottom: '10px' }}>
                <div style={{ width: `${conf ?? 0}%`, height: '100%', borderRadius: '3px', background: confColor }} />
              </div>
              {Object.keys(cb).length > 0 && (
                <div style={{ marginTop: '10px' }}>
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
                borderRadius: '10px', padding: '14px 16px', marginBottom: '16px',
              }}>
                <SL>Reasoning</SL>
                <p style={{ fontSize: '13px', color: 'var(--text)', margin: 0, lineHeight: 1.7 }}>
                  {d.reasoning}
                </p>
              </div>
            )}

            {/* Risk assessment */}
            {d.risk_assessment && (
              <div style={{
                display: 'flex', gap: '10px', alignItems: 'flex-start', marginBottom: '16px',
                background: 'var(--amber-bg)', border: '1px solid var(--amber-border)',
                borderRadius: '10px', padding: '12px 14px',
              }}>
                <span className="material-symbols-outlined" style={{ fontSize: '16px', color: 'var(--amber)', marginTop: '1px', flexShrink: 0 }}>warning</span>
                <p style={{ fontSize: '13px', color: 'var(--text-2)', margin: 0, lineHeight: 1.6 }}>
                  {d.risk_assessment}
                </p>
              </div>
            )}

            {/* Action details + Portfolio context — two columns when both exist */}
            <div style={{ display: 'grid', gridTemplateColumns: Object.keys(ad).length > 0 ? '1fr 1fr' : '1fr', gap: '0 24px' }}>
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

              <Section label="Portfolio Context">
                <KVRow label="Macro Regime"   value={ctx.macro_regime} />
                <KVRow label="Gross"          value={fmtPct(ctx.gross_exposure)} mono />
                <KVRow label="Net"            value={fmtPct(ctx.net_exposure)}   mono />
                <KVRow label="Cash"           value={fmtPct(ctx.cash_pct)}       mono />
                <KVRow label="Positions"      value={ctx.position_count}         mono />
                <KVRow label="Critical Alerts" value={ctx.active_critical_alerts} mono
                  color={ctx.active_critical_alerts > 0 ? 'var(--red)' : undefined}
                />
              </Section>
            </div>

            {/* Hard blocks */}
            {Object.keys(hb).length > 0 && (
              <Section label="Hard Block Checks">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 24px' }}>
                  {Object.entries(hb).map(([k, v]) => (
                    <KVRow
                      key={k}
                      label={k.replace(/_/g, ' ')}
                      value={v === true ? '✓ Pass' : v === false ? '✗ Fail' : String(v)}
                      color={v === true ? 'var(--green)' : v === false ? 'var(--red)' : undefined}
                    />
                  ))}
                </div>
              </Section>
            )}

            {/* Human override record */}
            {ho && (
              <Section label="Human Override">
                <KVRow label="Type" value={ho.override_type?.replace(/_/g, ' ')} />
                <KVRow label="When" value={fmtTime(ho.timestamp)} mono />
                {ho.reason && (
                  <p style={{ fontSize: '12px', color: 'var(--text-2)', marginTop: '8px', lineHeight: 1.6, fontStyle: 'italic' }}>
                    "{ho.reason}"
                  </p>
                )}
              </Section>
            )}
          </div>

          {/* Action bar — only for PENDING_HUMAN */}
          {execStatus === 'PENDING_HUMAN' && isAdmin && (
            <div style={{
              padding: '14px 24px', borderTop: '1px solid var(--border)',
              background: 'var(--surface-2)', display: 'flex', gap: '10px', flexShrink: 0,
            }}>
              <button
                disabled={!!loading}
                onClick={() => doOverride('FORCE_EXECUTE')}
                style={{
                  flex: 1, fontSize: '11px', fontWeight: 700, fontFamily: 'Syne',
                  padding: '9px 0', borderRadius: '8px', border: '1px solid var(--green-border)',
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
                  padding: '9px 20px', fontSize: '11px', fontWeight: 700, fontFamily: 'Syne',
                  borderRadius: '8px', border: '1px solid var(--amber-border)',
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
                  padding: '9px 20px', fontSize: '11px', fontWeight: 700, fontFamily: 'Syne',
                  borderRadius: '8px', border: '1px solid var(--red-border)',
                  background: 'var(--red-bg)', color: 'var(--red)',
                  cursor: 'pointer', opacity: loading && loading !== 'BLOCK' ? 0.5 : 1,
                }}
              >
                {loading === 'BLOCK' ? 'Blocking…' : 'Block'}
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
