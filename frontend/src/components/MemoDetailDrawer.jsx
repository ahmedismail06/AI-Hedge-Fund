import { useState } from 'react';
import { updateMemoStatus } from '../api/research';
import { useAuth } from '../context/AuthContext';

const VERDICT_STYLES = {
  LONG:  { bg: 'var(--green-bg)',  color: 'var(--green)',  border: 'var(--green-border)' },
  SHORT: { bg: 'var(--red-bg)',    color: 'var(--red)',    border: 'var(--red-border)' },
  AVOID: { bg: 'var(--surface-2)', color: 'var(--text-3)', border: 'var(--border)' },
};

const STATUS_LABEL = {
  PENDING_PM_REVIEW: { label: 'Pending PM Review', color: 'var(--amber)' },
  APPROVED:          { label: 'Approved',           color: 'var(--green)' },
  REJECTED:          { label: 'Rejected',           color: 'var(--red)' },
  WATCHLIST:         { label: 'Watchlist',          color: 'var(--accent)' },
  DEFERRED:          { label: 'Deferred',           color: 'var(--text-3)' },
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

function BulletList({ items, color = 'var(--text-2)' }) {
  if (!items?.length) return <span style={{ fontSize: '11px', color: 'var(--text-3)' }}>—</span>;
  return (
    <ul style={{ paddingLeft: '12px', margin: 0 }}>
      {items.map((item, i) => (
        <li key={i} style={{ fontSize: '12px', color, lineHeight: 1.6, marginBottom: '2px' }}>
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
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 0', borderBottom: '1px solid var(--border)' }}>
      <span style={{ fontSize: '11px', color: 'var(--text-3)' }}>{label}</span>
      <span style={{ fontSize: '11px', fontWeight: 700, fontFamily: 'Syne', textTransform: 'capitalize', color: colorMap[value] ?? 'var(--text-2)' }}>
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

  const m = memo.memo_json ?? memo;
  const memoId = memo.id;
  const fh = m.financial_health ?? {};
  const vs = VERDICT_STYLES[m.verdict] ?? VERDICT_STYLES.AVOID;
  const sl = STATUS_LABEL[status] ?? { label: status, color: 'var(--text-3)' };

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
        style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 100,
          backdropFilter: 'blur(2px)',
        }}
      />

      {/* Drawer */}
      <div
        style={{
          position: 'fixed', top: 0, right: 0, bottom: 0, width: '520px', maxWidth: '95vw',
          background: 'var(--surface)', borderLeft: '1px solid var(--border)',
          zIndex: 101, overflowY: 'auto', display: 'flex', flexDirection: 'column',
        }}
      >
        {/* Header */}
        <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: '22px', fontWeight: 800, color: 'var(--text)' }}>
                {m.ticker}
              </span>
              {m.verdict && (
                <span style={{
                  fontSize: '10px', fontWeight: 700, fontFamily: 'Syne', padding: '3px 9px',
                  borderRadius: '5px', background: vs.bg, color: vs.color, border: `1px solid ${vs.border}`,
                }}>
                  {m.verdict}
                </span>
              )}
              {m.conviction_score != null && (
                <span style={{
                  fontFamily: 'JetBrains Mono', fontSize: '13px', fontWeight: 700,
                  color: m.conviction_score >= 8 ? 'var(--green)' : m.conviction_score >= 7 ? 'var(--accent)' : 'var(--amber)',
                }}>
                  {m.conviction_score?.toFixed(1)} / 10
                </span>
              )}
              <span style={{ fontSize: '10px', color: sl.color, fontFamily: 'Syne', fontWeight: 700 }}>
                ● {sl.label}
              </span>
            </div>
            <button
              onClick={onClose}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-3)', padding: '2px', flexShrink: 0 }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '20px' }}>close</span>
            </button>
          </div>
          <div style={{ marginTop: '4px', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            {m.sector && <span style={{ fontSize: '10px', color: 'var(--text-3)', fontFamily: 'Syne' }}>{m.sector}</span>}
            {m.date && <span style={{ fontSize: '10px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>{m.date}</span>}
            {m.suggested_position_size && m.suggested_position_size !== 'skip' && (
              <span style={{ fontSize: '10px', fontFamily: 'Syne', fontWeight: 700, color: 'var(--accent)', textTransform: 'uppercase' }}>
                Size: {m.suggested_position_size}
              </span>
            )}
          </div>
        </div>

        {/* Body */}
        <div style={{ padding: '16px 20px', flex: 1 }}>

          {/* Company overview */}
          {m.company_overview && (
            <div style={{ marginBottom: '12px' }}>
              <p style={{ fontSize: '12px', color: 'var(--text-2)', lineHeight: 1.6, margin: 0 }}>
                {m.company_overview}
              </p>
            </div>
          )}

          {/* Variant perception */}
          {m.variant_perception && (
            <div style={{
              background: 'var(--surface-2)', border: '1px solid var(--border)',
              borderRadius: '8px', padding: '10px 12px', marginBottom: '12px',
            }}>
              <SL>Variant Perception</SL>
              <p style={{ fontSize: '12px', color: 'var(--text)', fontStyle: 'italic', margin: 0, lineHeight: 1.55 }}>
                "{m.variant_perception}"
              </p>
            </div>
          )}

          {/* Repricing catalyst */}
          {m.repricing_catalyst && (
            <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', marginBottom: '12px' }}>
              <span className="material-symbols-outlined" style={{ fontSize: '14px', color: 'var(--accent)', marginTop: '1px', flexShrink: 0 }}>bolt</span>
              <p style={{ fontSize: '12px', color: 'var(--text-2)', margin: 0, lineHeight: 1.55 }}>
                {m.repricing_catalyst}
              </p>
            </div>
          )}

          {/* Bull thesis */}
          <Section label="Bull Thesis">
            <BulletList items={m.bull_thesis} color="var(--green)" />
          </Section>

          {/* Bear thesis */}
          <Section label="Bear Thesis">
            <BulletList items={m.bear_thesis} color="var(--red)" />
          </Section>

          {/* Key risks */}
          {m.key_risks?.length > 0 && (
            <Section label="Key Risks">
              <BulletList items={m.key_risks} color="var(--amber)" />
            </Section>
          )}

          {/* Catalysts */}
          {m.catalysts?.length > 0 && (
            <Section label="Catalysts">
              <BulletList items={m.catalysts} />
            </Section>
          )}

          {/* Financial health */}
          <Section label="Financial Health">
            <HealthRow label="Revenue Trend"  value={fh.revenue_trend} />
            <HealthRow label="Margin Trend"   value={fh.margin_trend} />
            <HealthRow label="Debt Level"     value={fh.debt_level} />
            <HealthRow label="Free Cash Flow" value={fh.fcf} />
            {fh.cash_runway_months != null && (
              <HealthRow label="Cash Runway" value={`${fh.cash_runway_months} mo`} />
            )}
          </Section>

          {/* Valuation */}
          {(m.valuation_note || m.price_target) && (
            <Section label="Valuation">
              {m.price_target && (
                <div style={{ marginBottom: '6px', display: 'flex', gap: '8px', alignItems: 'baseline' }}>
                  <span style={{ fontFamily: 'JetBrains Mono', fontSize: '20px', fontWeight: 800, color: 'var(--text)' }}>
                    ${m.price_target}
                  </span>
                  {m.price_target_basis && (
                    <span style={{ fontSize: '10px', color: 'var(--text-3)' }}>{m.price_target_basis}</span>
                  )}
                </div>
              )}
              {m.valuation_note && (
                <p style={{ fontSize: '12px', color: 'var(--text-2)', margin: 0, lineHeight: 1.55 }}>{m.valuation_note}</p>
              )}
            </Section>
          )}

          {/* DCF snapshot */}
          {m.dcf && (
            <Section label="DCF Scenarios">
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px' }}>
                {[['Bear', m.dcf.bear_target, 'var(--red)'], ['Base', m.dcf.base_target, 'var(--text)'], ['Bull', m.dcf.bull_target, 'var(--green)']].map(([label, val, color]) => (
                  <div key={label} style={{ textAlign: 'center', padding: '8px', background: 'var(--surface-2)', borderRadius: '6px', border: '1px solid var(--border)' }}>
                    <div style={{ fontSize: '9px', fontFamily: 'Syne', fontWeight: 700, color: 'var(--text-3)', marginBottom: '2px' }}>{label}</div>
                    <div style={{ fontFamily: 'JetBrains Mono', fontSize: '14px', fontWeight: 800, color }}>${val}</div>
                  </div>
                ))}
              </div>
              {m.dcf.wacc && (
                <div style={{ marginTop: '6px', fontSize: '10px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
                  WACC {(m.dcf.wacc * 100).toFixed(1)}% · TGR {(m.dcf.terminal_growth * 100).toFixed(1)}%
                </div>
              )}
            </Section>
          )}

          {/* Macro sensitivity */}
          {m.macro_sensitivity && (
            <Section label="Macro Sensitivity">
              <p style={{ fontSize: '12px', color: 'var(--text-2)', margin: 0, lineHeight: 1.55 }}>{m.macro_sensitivity}</p>
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
              <p style={{ fontSize: '12px', color: 'var(--text-2)', margin: 0, lineHeight: 1.55, fontStyle: 'italic' }}>
                {m.conviction_score_rationale}
              </p>
            </Section>
          )}

          {/* Summary */}
          {m.summary && (
            <Section label="Summary">
              <p style={{ fontSize: '12px', color: 'var(--text-2)', margin: 0, lineHeight: 1.55 }}>{m.summary}</p>
            </Section>
          )}
        </div>

        {/* Action bar */}
        <div style={{
          padding: '12px 20px', borderTop: '1px solid var(--border)',
          background: 'var(--surface-2)', display: 'flex', gap: '8px', alignItems: 'center',
          position: 'sticky', bottom: 0,
        }}>
          {!isAdmin && (
            <span style={{ fontSize: '10px', color: 'var(--text-3)', fontFamily: 'Syne' }}>Guest — read only</span>
          )}
          {isAdmin && (
            <>
              <button
                disabled={!!loading || status === 'APPROVED'}
                onClick={() => handleAction('APPROVED')}
                style={{
                  fontSize: '10px', fontWeight: 700, fontFamily: 'Syne', padding: '6px 14px',
                  borderRadius: '6px', border: 'none', cursor: status === 'APPROVED' ? 'default' : 'pointer',
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
                  fontSize: '10px', fontWeight: 700, fontFamily: 'Syne', padding: '6px 14px',
                  borderRadius: '6px', border: 'none', cursor: status === 'WATCHLIST' ? 'default' : 'pointer',
                  background: status === 'WATCHLIST' ? 'var(--surface-3)' : 'var(--accent)',
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
                  fontSize: '10px', fontWeight: 700, fontFamily: 'Syne', padding: '6px 14px',
                  borderRadius: '6px', border: '1px solid var(--red-border)',
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
    </>
  );
}
