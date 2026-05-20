import { useEffect } from 'react';
import PriceChart from './PriceChart';
import ConvictionBadge from './ConvictionBadge';

const fmt$ = (v) => {
  if (v == null) return '—';
  const abs = Math.abs(v);
  const prefix = v < 0 ? '-' : '';
  if (abs >= 1_000_000) return `${prefix}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000)     return `${prefix}$${(abs / 1_000).toFixed(1)}k`;
  return `${prefix}$${abs.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
};

const fmtPct = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`;

function SL({ children }) {
  return (
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
      {children}
    </div>
  );
}

function Stat({ label, value, color }) {
  return (
    <div>
      <SL>{label}</SL>
      <div style={{ fontFamily: 'JetBrains Mono', fontSize: '14px', fontWeight: 700, color: color ?? 'var(--text)', marginTop: '2px' }}>
        {value}
      </div>
    </div>
  );
}

const VERDICT_COLORS = {
  LONG:  { bg: 'var(--green-bg)', color: 'var(--green)', border: 'var(--green-border)' },
  SHORT: { bg: 'var(--red-bg)',   color: 'var(--red)',   border: 'var(--red-border)' },
  AVOID: { bg: 'var(--surface-2)',color: 'var(--text-3)',border: 'var(--border)' },
};

export default function TickerDrawer({ position, memo, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!position) return null;

  const p = position;
  const isLong = p.direction === 'LONG';
  const entry = p.entry_price ?? 0;
  const current = p.current_price ?? entry;
  const shares = p.share_count ?? 0;
  const pnl = isLong ? (current - entry) * shares : (entry - current) * shares;
  const pnlPct = entry > 0 ? (isLong ? (current - entry) / entry : (entry - current) / entry) : 0;
  const pnlPos = pnl >= 0;
  const stopDist = entry > 0 && p.stop_loss_price
    ? ((p.stop_loss_price - entry) / entry)
    : null;

  const verdictStyle = VERDICT_COLORS[memo?.verdict] ?? VERDICT_COLORS.AVOID;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40"
        style={{ background: 'rgba(0,0,0,0.35)' }}
        onClick={onClose}
      />

      {/* Drawer */}
      <div
        className="fixed top-0 right-0 h-screen z-50 flex flex-col animate-slide-right term-scroll"
        style={{
          width: '400px',
          background: 'var(--surface)',
          borderLeft: '1px solid var(--border)',
          boxShadow: '-8px 0 32px rgba(0,0,0,0.35)',
          overflowY: 'auto',
        }}
      >
        {/* Header */}
        <div
          className="flex items-center gap-3 px-5 py-4 flex-shrink-0"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          <div className="flex-1 min-w-0">
            <div style={{ fontFamily: 'JetBrains Mono', fontSize: '20px', fontWeight: 800, color: 'var(--text)' }}>
              {p.ticker}
            </div>
            <div className="flex items-center gap-2 mt-1">
              <span
                style={{
                  fontSize: '10px', fontWeight: 700, fontFamily: 'Syne',
                  padding: '1px 7px', borderRadius: '4px',
                  background: isLong ? 'var(--blue-bg)' : 'var(--red-bg)',
                  color: isLong ? 'var(--blue)' : 'var(--red)',
                  border: `1px solid ${isLong ? 'var(--blue-border)' : 'var(--red-border)'}`,
                }}
              >
                {p.direction}
              </span>
              {memo?.verdict && (
                <span style={{
                  fontSize: '10px', fontWeight: 700, fontFamily: 'Syne',
                  padding: '1px 7px', borderRadius: '4px',
                  background: verdictStyle.bg, color: verdictStyle.color,
                  border: `1px solid ${verdictStyle.border}`,
                }}>
                  {memo.verdict}
                </span>
              )}
              {memo?.conviction_score != null && (
                <ConvictionBadge score={memo.conviction_score} />
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{ color: 'var(--text-2)', background: 'none', border: 'none', cursor: 'pointer', padding: '4px' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--text)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-2)'}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '20px' }}>close</span>
          </button>
        </div>

        {/* Chart */}
        <div className="px-5 py-4" style={{ borderBottom: '1px solid var(--border)' }}>
          <PriceChart ticker={p.ticker} height={180} defaultRange="1M" />
        </div>

        {/* P&L summary */}
        <div
          className="mx-5 mt-4 rounded-xl p-4"
          style={{
            background: pnlPos ? 'var(--green-bg)' : 'var(--red-bg)',
            border: `1px solid ${pnlPos ? 'var(--green-border)' : 'var(--red-border)'}`,
          }}
        >
          <div className="flex items-center justify-between">
            <div>
              <SL>Unrealized P&L</SL>
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: '22px', fontWeight: 800, color: pnlPos ? 'var(--green)' : 'var(--red)', marginTop: '2px' }}>
                {pnlPos ? '+' : ''}{fmt$(pnl)}
              </div>
            </div>
            <div style={{ textAlign: 'right' }}>
              <SL>Return</SL>
              <div style={{ fontFamily: 'JetBrains Mono', fontSize: '18px', fontWeight: 700, color: pnlPos ? 'var(--green)' : 'var(--red)', marginTop: '2px' }}>
                {fmtPct(pnlPct)}
              </div>
            </div>
          </div>
        </div>

        {/* Position stats grid */}
        <div className="px-5 mt-4 grid grid-cols-2 gap-x-4 gap-y-4">
          <Stat label="Entry Price"  value={fmt$(entry)} />
          <Stat label="Current Price" value={fmt$(current)} />
          <Stat label="Shares" value={shares.toLocaleString()} />
          <Stat label="Position Weight" value={p.size_label ?? '—'} />
          <Stat
            label="Stop Price"
            value={fmt$(p.stop_loss_price)}
            color={stopDist != null && Math.abs(stopDist) < 0.05 ? 'var(--red)' : 'var(--text)'}
          />
          <Stat
            label="Distance to Stop"
            value={stopDist != null ? `${(stopDist * 100).toFixed(1)}%` : '—'}
            color={stopDist != null && Math.abs(stopDist) < 0.05 ? 'var(--red)' : 'var(--text-2)'}
          />
        </div>

        {/* Stop distance bar */}
        {stopDist != null && (
          <div className="px-5 mt-4">
            <SL>Stop Distance</SL>
            <div className="mt-2 rounded-full overflow-hidden" style={{ height: '6px', background: 'var(--border)' }}>
              <div
                style={{
                  width: `${Math.min(Math.abs(stopDist) * 400, 100)}%`,
                  height: '100%',
                  background: Math.abs(stopDist) < 0.03 ? 'var(--red)' : Math.abs(stopDist) < 0.06 ? 'var(--amber)' : 'var(--green)',
                  transition: 'width 0.4s ease',
                }}
              />
            </div>
            <div className="flex justify-between mt-1">
              <span style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>Stop</span>
              <span style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>Entry</span>
            </div>
          </div>
        )}

        {/* Research memo section */}
        {memo && (
          <div className="px-5 mt-5 pb-6 space-y-4" style={{ borderTop: '1px solid var(--border)', paddingTop: '16px' }}>
            {memo.thesis && (
              <div>
                <SL>Investment Thesis</SL>
                <p style={{ fontSize: '12px', color: 'var(--text-2)', marginTop: '4px', lineHeight: '1.6' }}>
                  {memo.thesis}
                </p>
              </div>
            )}
            {memo.variant_perception && (
              <div>
                <SL>Variant Perception</SL>
                <p style={{ fontSize: '12px', color: 'var(--text-2)', marginTop: '4px', lineHeight: '1.6', fontStyle: 'italic' }}>
                  "{memo.variant_perception}"
                </p>
              </div>
            )}
            {memo.repricing_catalyst && (
              <div>
                <SL>Repricing Catalyst</SL>
                <p style={{ fontSize: '12px', color: 'var(--text)', marginTop: '4px', lineHeight: '1.6' }}>
                  {memo.repricing_catalyst}
                </p>
              </div>
            )}
          </div>
        )}

        {!memo && (
          <div className="px-5 mt-4 pb-6">
            <p style={{ fontSize: '12px', color: 'var(--text-3)', fontStyle: 'italic' }}>
              No research memo available for this position.
            </p>
          </div>
        )}
      </div>
    </>
  );
}
