import { useState } from 'react';
import { updateMemoStatus } from '../api/research';
import { useAuth } from '../context/AuthContext';

const VERDICT_STYLES = {
  LONG:  { bg: 'var(--green-bg)',  color: 'var(--green)',  border: 'var(--green-border)' },
  SHORT: { bg: 'var(--red-bg)',    color: 'var(--red)',    border: 'var(--red-border)' },
  AVOID: { bg: 'var(--surface-2)', color: 'var(--text-3)', border: 'var(--border)' },
};

const STATUS_LABEL = {
  PENDING_PM_REVIEW: { label: 'Pending Review', color: 'var(--amber)' },
  APPROVED:          { label: 'Approved',        color: 'var(--green)' },
  REJECTED:          { label: 'Rejected',        color: 'var(--red)' },
  WATCHLIST:         { label: 'Watchlist',       color: 'var(--accent)' },
  DEFERRED:          { label: 'Deferred',        color: 'var(--text-3)' },
};

function SL({ children }) {
  return (
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne', marginBottom: '8px' }}>
      {children}
    </div>
  );
}

function Section({ label, children, style = {} }) {
  return (
    <div style={{ borderTop: '1px solid var(--border)', paddingTop: '14px', marginTop: '14px', ...style }}>
      {label && <SL>{label}</SL>}
      {children}
    </div>
  );
}

function BulletList({ items, color = 'var(--text-2)' }) {
  if (!items?.length) return <span style={{ fontSize: '12px', color: 'var(--text-3)' }}>—</span>;
  return (
    <ul style={{ paddingLeft: '14px', margin: 0 }}>
      {items.map((item, i) => (
        <li key={i} style={{ fontSize: '13px', color, lineHeight: 1.65, marginBottom: '4px' }}>
          {item}
        </li>
      ))}
    </ul>
  );
}

function HealthRow({ label, value }) {
  const colorMap = {
    growing: 'var(--green)', expanding: 'var(--green)', low: 'var(--green)', strong: 'var(--green)',
    declining: 'var(--red)', contracting: 'var(--red)', high: 'var(--red)', weak: 'var(--red)',
    stable: 'var(--text-2)', moderate: 'var(--amber)', neutral: 'var(--text-2)',
  };
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
      <span style={{ fontSize: '12px', color: 'var(--text-3)' }}>{label}</span>
      <span style={{ fontSize: '12px', fontWeight: 700, fontFamily: 'Syne', textTransform: 'capitalize', color: colorMap[value?.toLowerCase?.()] ?? 'var(--text-2)' }}>
        {value ?? '—'}
      </span>
    </div>
  );
}

export default function MemoDetailDrawer({ memo, onClose }) {
  const { isAdmin } = useAuth();
  const [status, setStatus] = useState(memo?.status ?? 'PENDING_PM_REVIEW');
  const [loading, setLoading] = useState(null);

  if (!memo) return null;

  const m      = memo.memo_json ?? memo;
  const memoId = memo.id;
  const fh     = m.financial_health ?? {};
  const vs     = VERDICT_STYLES[m.verdict] ?? VERDICT_STYLES.AVOID;
  const sl     = STATUS_LABEL[status] ?? { label: status, color: 'var(--text-3)' };
  const convScore = m.conviction_score;
  const convColor = convScore >= 8 ? 'var(--green)' : convScore >= 7 ? 'var(--accent)' : 'var(--amber)';

  async function handleAction(newStatus) {
    if (!memoId) return;
    setLoading(newStatus);
    try {
      await updateMemoStatus(memoId, newStatus);
      setStatus(newStatus);
    } finally {
      setLoading(null);
    }
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
        padding: '24px', pointerEvents: 'none',
      }}>
        <div style={{
          width: '100%', maxWidth: '780px', maxHeight: '90vh',
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: '16px', display: 'flex', flexDirection: 'column',
          pointerEvents: 'auto', overflow: 'hidden',
          boxShadow: '0 24px 64px rgba(0,0,0,0.4)',
        }}>

          {/* ── Header ── */}
          <div style={{
            padding: '20px 26px 16px', borderBottom: '1px solid var(--border)',
            background: 'var(--surface)', flexShrink: 0,
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '10px' }}>
                <span style={{ fontFamily: 'JetBrains Mono', fontSize: '26px', fontWeight: 800, color: 'var(--text)' }}>
                  {m.ticker}
                </span>
                {m.verdict && (
                  <span style={{
                    fontSize: '11px', fontWeight: 700, fontFamily: 'Syne',
                    padding: '4px 10px', borderRadius: '6px',
                    background: vs.bg, color: vs.color, border: `1px solid ${vs.border}`,
                  }}>
                    {m.verdict}
                  </span>
                )}
                {convScore != null && (
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: '20px', fontWeight: 800, color: convColor }}>
                    {convScore.toFixed(1)}<span style={{ fontSize: '13px', color: 'var(--text-3)', fontWeight: 400 }}>/10</span>
                  </span>
                )}
                <span style={{ fontSize: '11px', color: sl.color, fontFamily: 'Syne', fontWeight: 700 }}>
                  ● {sl.label}
                </span>
              </div>
              <button
                onClick={onClose}
                style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)', padding: '4px', flexShrink: 0, lineHeight: 1 }}
              >
                <span className="material-symbols-outlined" style={{ fontSize: '22px' }}>close</span>
              </button>
            </div>
            <div style={{ marginTop: '6px', display: 'flex', gap: '14px', flexWrap: 'wrap', alignItems: 'center' }}>
              {m.sector && <span style={{ fontSize: '11px', color: 'var(--text-3)', fontFamily: 'Syne' }}>{m.sector}</span>}
              {m.date && <span style={{ fontSize: '11px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>{m.date}</span>}
              {m.suggested_position_size && m.suggested_position_size !== 'skip' && (
                <span style={{ fontSize: '11px', fontFamily: 'Syne', fontWeight: 700, color: 'var(--accent)', textTransform: 'uppercase' }}>
                  Size: {m.suggested_position_size}
                </span>
              )}
            </div>
          </div>

          {/* ── Scrollable body ── */}
          <div style={{ padding: '20px 26px', flex: 1, overflowY: 'auto' }} className="term-scroll">

            {/* Company overview */}
            {m.company_overview && (
              <p style={{ fontSize: '13px', color: 'var(--text-2)', lineHeight: 1.7, margin: '0 0 14px' }}>
                {m.company_overview}
              </p>
            )}

            {/* Variant perception */}
            {m.variant_perception && (
              <div style={{
                background: 'var(--surface-2)', border: '1px solid var(--border)',
                borderRadius: '10px', padding: '14px 16px', marginBottom: '14px',
              }}>
                <SL>Variant Perception</SL>
                <p style={{ fontSize: '13px', color: 'var(--text)', fontStyle: 'italic', margin: 0, lineHeight: 1.65 }}>
                  "{m.variant_perception}"
                </p>
              </div>
            )}

            {/* Repricing catalyst */}
            {m.repricing_catalyst && (
              <div style={{
                display: 'flex', gap: '10px', alignItems: 'flex-start', marginBottom: '14px',
                background: 'var(--accent-muted)', border: '1px solid var(--accent-ring)',
                borderRadius: '10px', padding: '12px 14px',
              }}>
                <span className="material-symbols-outlined" style={{ fontSize: '16px', color: 'var(--accent)', marginTop: '1px', flexShrink: 0 }}>bolt</span>
                <p style={{ fontSize: '13px', color: 'var(--text-2)', margin: 0, lineHeight: 1.65 }}>
                  {m.repricing_catalyst}
                </p>
              </div>
            )}

            {/* Bull / Bear — side by side */}
            <Section label={undefined}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
                <div>
                  <SL>Bull Thesis</SL>
                  <BulletList items={m.bull_thesis} color="var(--green)" />
                </div>
                <div>
                  <SL>Bear Thesis</SL>
                  <BulletList items={m.bear_thesis} color="var(--red)" />
                </div>
              </div>
            </Section>

            {/* Key risks + Catalysts — side by side when both exist */}
            {(m.key_risks?.length > 0 || m.catalysts?.length > 0) && (
              <Section label={undefined}>
                <div style={{ display: 'grid', gridTemplateColumns: m.key_risks?.length > 0 && m.catalysts?.length > 0 ? '1fr 1fr' : '1fr', gap: '20px' }}>
                  {m.key_risks?.length > 0 && (
                    <div>
                      <SL>Key Risks</SL>
                      <BulletList items={m.key_risks} color="var(--amber)" />
                    </div>
                  )}
                  {m.catalysts?.length > 0 && (
                    <div>
                      <SL>Catalysts</SL>
                      <BulletList items={m.catalysts} />
                    </div>
                  )}
                </div>
              </Section>
            )}

            {/* Financial health + Valuation — side by side */}
            <Section label={undefined}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
                <div>
                  <SL>Financial Health</SL>
                  <HealthRow label="Revenue Trend"  value={fh.revenue_trend} />
                  <HealthRow label="Margin Trend"   value={fh.margin_trend} />
                  <HealthRow label="Debt Level"     value={fh.debt_level} />
                  <HealthRow label="Free Cash Flow" value={fh.fcf} />
                  {fh.cash_runway_months != null && (
                    <HealthRow label="Cash Runway" value={`${fh.cash_runway_months} mo`} />
                  )}
                </div>
                {(m.valuation_note || m.price_target) && (
                  <div>
                    <SL>Valuation</SL>
                    {m.price_target && (
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline', marginBottom: '8px' }}>
                        <span style={{ fontFamily: 'JetBrains Mono', fontSize: '24px', fontWeight: 800, color: 'var(--text)' }}>
                          ${m.price_target}
                        </span>
                        {m.price_target_basis && (
                          <span style={{ fontSize: '11px', color: 'var(--text-3)' }}>{m.price_target_basis}</span>
                        )}
                      </div>
                    )}
                    {m.valuation_note && (
                      <p style={{ fontSize: '13px', color: 'var(--text-2)', margin: 0, lineHeight: 1.6 }}>{m.valuation_note}</p>
                    )}
                  </div>
                )}
              </div>
            </Section>

            {/* DCF scenarios */}
            {m.dcf && (
              <Section label="DCF Scenarios">
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '10px' }}>
                  {[['Bear', m.dcf.bear_target, 'var(--red)'], ['Base', m.dcf.base_target, 'var(--text)'], ['Bull', m.dcf.bull_target, 'var(--green)']].map(([label, val, color]) => (
                    <div key={label} style={{ textAlign: 'center', padding: '12px 8px', background: 'var(--surface-2)', borderRadius: '8px', border: '1px solid var(--border)' }}>
                      <div style={{ fontSize: '9px', fontFamily: 'Syne', fontWeight: 700, color: 'var(--text-3)', marginBottom: '4px', textTransform: 'uppercase', letterSpacing: '0.1em' }}>{label}</div>
                      <div style={{ fontFamily: 'JetBrains Mono', fontSize: '18px', fontWeight: 800, color }}>${val}</div>
                    </div>
                  ))}
                </div>
                {m.dcf.wacc && (
                  <div style={{ marginTop: '8px', fontSize: '11px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
                    WACC {(m.dcf.wacc * 100).toFixed(1)}% · Terminal Growth {(m.dcf.terminal_growth * 100).toFixed(1)}%
                  </div>
                )}
              </Section>
            )}

            {/* Macro sensitivity */}
            {m.macro_sensitivity && (
              <Section label="Macro Sensitivity">
                <p style={{ fontSize: '13px', color: 'var(--text-2)', margin: 0, lineHeight: 1.65 }}>{m.macro_sensitivity}</p>
              </Section>
            )}

            {/* Red team */}
            {m.red_team_risks?.length > 0 && (
              <Section label="Red Team">
                <BulletList items={m.red_team_risks} color="var(--red)" />
              </Section>
            )}

            {/* Conviction rationale */}
            {m.conviction_score_rationale && (
              <Section label="Conviction Rationale">
                <p style={{ fontSize: '13px', color: 'var(--text-2)', margin: 0, lineHeight: 1.65, fontStyle: 'italic' }}>
                  {m.conviction_score_rationale}
                </p>
              </Section>
            )}

            {/* Summary */}
            {m.summary && (
              <Section label="Summary">
                <p style={{ fontSize: '13px', color: 'var(--text-2)', margin: 0, lineHeight: 1.65 }}>{m.summary}</p>
              </Section>
            )}
          </div>

          {/* ── Action bar ── */}
          <div style={{
            padding: '14px 26px', borderTop: '1px solid var(--border)',
            background: 'var(--surface-2)', display: 'flex', gap: '10px', alignItems: 'center', flexShrink: 0,
          }}>
            {!isAdmin ? (
              <span style={{ fontSize: '11px', color: 'var(--text-3)', fontFamily: 'Syne' }}>Guest — read only</span>
            ) : (
              <>
                <button
                  disabled={!!loading || status === 'APPROVED'}
                  onClick={() => handleAction('APPROVED')}
                  style={{
                    fontSize: '11px', fontWeight: 700, fontFamily: 'Syne', padding: '8px 18px',
                    borderRadius: '8px', border: 'none', cursor: status === 'APPROVED' ? 'default' : 'pointer',
                    background: status === 'APPROVED' ? 'var(--green-bg)' : 'var(--green)',
                    color: status === 'APPROVED' ? 'var(--green)' : '#000',
                    opacity: loading && loading !== 'APPROVED' ? 0.5 : 1,
                  }}
                >
                  {loading === 'APPROVED' ? 'Approving…' : 'Approve'}
                </button>
                <button
                  disabled={!!loading || status === 'WATCHLIST'}
                  onClick={() => handleAction('WATCHLIST')}
                  style={{
                    fontSize: '11px', fontWeight: 700, fontFamily: 'Syne', padding: '8px 18px',
                    borderRadius: '8px', border: 'none', cursor: status === 'WATCHLIST' ? 'default' : 'pointer',
                    background: status === 'WATCHLIST' ? 'var(--surface-2)' : 'var(--accent)',
                    color: status === 'WATCHLIST' ? 'var(--accent)' : '#000',
                    opacity: loading && loading !== 'WATCHLIST' ? 0.5 : 1,
                  }}
                >
                  {loading === 'WATCHLIST' ? 'Adding…' : 'Watchlist'}
                </button>
                <button
                  disabled={!!loading || status === 'REJECTED'}
                  onClick={() => handleAction('REJECTED')}
                  style={{
                    fontSize: '11px', fontWeight: 700, fontFamily: 'Syne', padding: '8px 18px',
                    borderRadius: '8px', border: '1px solid var(--red-border)',
                    cursor: status === 'REJECTED' ? 'default' : 'pointer',
                    background: status === 'REJECTED' ? 'var(--red-bg)' : 'transparent',
                    color: 'var(--red)',
                    opacity: loading && loading !== 'REJECTED' ? 0.5 : 1,
                  }}
                >
                  {loading === 'REJECTED' ? 'Rejecting…' : 'Reject'}
                </button>
              </>
            )}
          </div>

        </div>
      </div>
    </>
  );
}
