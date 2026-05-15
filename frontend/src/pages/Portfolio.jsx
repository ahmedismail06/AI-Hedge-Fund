import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { getPositions, getPending, getExposure, getHistory, approveTrade, rejectTrade } from '../api/portfolio';
import { getRegime } from '../api/macro';
import ExposureBar, { REGIME_CAPS } from '../components/ExposureBar';
import PositionRow from '../components/PositionRow';
import ConfirmDialog from '../components/ConfirmDialog';

const STOP_TIERS = {
  normal:  { tier1: -8,  tier2: -15, tier3: -20 },
  riskOff: { tier1: -5,  tier2: -10, tier3: -15 },
};

const fmt$ = (v) =>
  v == null ? '—' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 0 }).format(v);

function exportCSV(rows, filename) {
  if (!rows.length) return;
  const keys = Object.keys(rows[0]);
  const csv = [keys.join(','), ...rows.map(r => keys.map(k => JSON.stringify(r[k] ?? '')).join(','))].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

function SL({ children }) {
  return (
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
      {children}
    </div>
  );
}

function Card({ children, className = '', style = {} }) {
  return (
    <div className={`rounded-xl p-5 ${className}`} style={{ background: 'var(--surface)', border: '1px solid var(--border)', ...style }}>
      {children}
    </div>
  );
}

function VerdictBadge({ verdict }) {
  const styles = {
    LONG:  { background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-border)' },
    SHORT: { background: 'var(--red-bg)',   color: 'var(--red)',   border: '1px solid var(--red-border)' },
    AVOID: { background: 'var(--surface-2)', color: 'var(--text-3)', border: '1px solid var(--border)' },
  };
  const s = styles[verdict] ?? styles.AVOID;
  return (
    <span style={{ ...s, fontSize: '11px', fontWeight: 600, padding: '2px 8px', borderRadius: '999px', fontFamily: 'Syne' }}>
      {verdict}
    </span>
  );
}

function ConvictionBadge({ score }) {
  const s = score >= 8
    ? { background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-border)' }
    : score >= 6
    ? { background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-border)' }
    : { background: 'var(--red-bg)',   color: 'var(--red)',   border: '1px solid var(--red-border)' };
  return (
    <span style={{ ...s, fontSize: '11px', fontWeight: 600, padding: '2px 8px', borderRadius: '999px', fontFamily: 'Syne' }}>
      Conviction {score?.toFixed(1)}/10
    </span>
  );
}

function PendingCard({ item, regime, onApprove, onReject, isAdmin }) {
  const isRiskOff = regime && (regime.regime === 'Risk-Off' || regime.regime === 'Stagflation');
  const tiers = isRiskOff ? STOP_TIERS.riskOff : STOP_TIERS.normal;

  return (
    <div className="rounded-xl p-5" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span style={{ fontFamily: 'JetBrains Mono', fontWeight: 700, fontSize: '20px', color: 'var(--text)' }}>
              {item.ticker}
            </span>
            <VerdictBadge verdict={item.verdict} />
            <ConvictionBadge score={item.conviction_score} />
          </div>
          {item.size_label && (
            <p className="text-sm mt-1" style={{ color: 'var(--text-2)' }}>
              <strong style={{ color: 'var(--text)' }}>{item.size_label} position</strong> —{' '}
              {({ Large: '8%', Medium: '5%', Small: '2%', Micro: '1%' })[item.size_label] ?? ''} of portfolio
              {item.portfolio_value
                ? ` (~${fmt$(item.portfolio_value * ({ Large: 0.08, Medium: 0.05, Small: 0.02, Micro: 0.01 }[item.size_label] ?? 0))})`
                : ''}
            </p>
          )}
        </div>
        <div className="flex gap-2 flex-shrink-0">
          <button
            onClick={() => onApprove(item.id)}
            disabled={!isAdmin}
            title={!isAdmin ? 'Guest mode — read only' : undefined}
            style={{
              padding: '6px 16px',
              fontSize: '13px',
              fontWeight: 600,
              fontFamily: 'Syne',
              background: 'var(--green-bg)',
              color: 'var(--green)',
              border: '1px solid var(--green-border)',
              borderRadius: '8px',
              cursor: !isAdmin ? 'not-allowed' : 'pointer',
              opacity: !isAdmin ? 0.4 : 1,
            }}
          >
            Approve
          </button>
          <button
            onClick={() => onReject(item.id)}
            disabled={!isAdmin}
            title={!isAdmin ? 'Guest mode — read only' : undefined}
            style={{
              padding: '6px 16px',
              fontSize: '13px',
              fontWeight: 600,
              fontFamily: 'Syne',
              background: 'var(--surface-2)',
              color: 'var(--text-2)',
              border: '1px solid var(--border)',
              borderRadius: '8px',
              cursor: !isAdmin ? 'not-allowed' : 'pointer',
              opacity: !isAdmin ? 0.4 : 1,
            }}
            onMouseEnter={e => {
              if (!isAdmin) return;
              e.currentTarget.style.background = 'var(--red-bg)';
              e.currentTarget.style.color = 'var(--red)';
              e.currentTarget.style.borderColor = 'var(--red-border)';
            }}
            onMouseLeave={e => {
              e.currentTarget.style.background = 'var(--surface-2)';
              e.currentTarget.style.color = 'var(--text-2)';
              e.currentTarget.style.borderColor = 'var(--border)';
            }}
          >
            Reject
          </button>
        </div>
      </div>

      {/* Stop-loss ladder */}
      <div className="rounded-lg p-3 space-y-2" style={{ background: 'var(--surface-2)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
          <SL>Stop-Loss Protection</SL>
          {isRiskOff && (
            <span style={{
              fontSize: '11px', fontWeight: 600, fontFamily: 'Syne',
              background: 'var(--amber-bg)', color: 'var(--amber)',
              border: '1px solid var(--amber-border)',
              padding: '1px 8px', borderRadius: '999px',
            }}>
              Tighter — {regime.regime} Mode
            </span>
          )}
        </div>
        {[
          { label: 'Tier 1 — Position Stop', pct: tiers.tier1, desc: 'Auto-sell this position if price falls this much from entry' },
          { label: 'Tier 2 — Strategy Stop', pct: tiers.tier2, desc: 'Close all strategy positions if strategy P&L hits this level' },
          { label: 'Tier 3 — Portfolio Stop', pct: tiers.tier3, desc: 'Halt all trading if portfolio drawdown reaches this level' },
        ].map(({ label, pct, desc }) => (
          <div key={label} className="flex items-center gap-3 text-sm">
            <span style={{ width: '160px', fontSize: '11px', fontWeight: 500, color: 'var(--text-2)', flexShrink: 0, fontFamily: 'Syne' }}>
              {label}
            </span>
            <span style={{ fontFamily: 'JetBrains Mono', fontWeight: 700, color: 'var(--red)', width: '40px', textAlign: 'right' }}>
              {pct}%
            </span>
            <span style={{ fontSize: '11px', color: 'var(--text-3)' }}>{desc}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Portfolio() {
  const { isAdmin } = useAuth();
  const [tab, setTab] = useState('active');
  const [pending, setPending] = useState([]);
  const [positions, setPositions] = useState([]);
  const [closed, setClosed] = useState([]);
  const [exposure, setExposure] = useState(null);
  const [regime, setRegime] = useState(null);
  const [confirm, setConfirm] = useState(null);

  const loadPending = useCallback(async () => {
    try { setPending(await getPending()); } catch {}
  }, []);

  const loadPositions = useCallback(async () => {
    try { setPositions(await getPositions()); } catch {}
  }, []);

  useEffect(() => {
    const init = async () => {
      try {
        const [exp, reg, hist] = await Promise.all([getExposure(), getRegime(), getHistory()]);
        setExposure(exp);
        setRegime(reg);
        setClosed(Array.isArray(hist) ? hist : []);
      } catch {}
    };
    init();
    loadPending();
    loadPositions();
    const t1 = setInterval(loadPending, 30000);
    const t2 = setInterval(loadPositions, 60000);
    return () => { clearInterval(t1); clearInterval(t2); };
  }, [loadPending, loadPositions]);

  const handleConfirm = async () => {
    if (!confirm) return;
    const { action, id } = confirm;
    setPending(prev => prev.filter(p => p.id !== id));
    setConfirm(null);
    try {
      if (action === 'approve') await approveTrade(id);
      else await rejectTrade(id);
    } catch { loadPending(); }
  };

  // Group multiple positions for the same ticker
  const groupedPositions = positions.reduce((acc, p) => {
    if (!acc[p.ticker]) {
      acc[p.ticker] = { ...p };
    } else {
      const existing = acc[p.ticker];
      const totalShares = (existing.share_count || 0) + (p.share_count || 0);
      if (totalShares > 0) {
        existing.entry_price = ((existing.entry_price || 0) * (existing.share_count || 0) + (p.entry_price || 0) * (p.share_count || 0)) / totalShares;
      }
      existing.share_count = totalShares;
    }
    return acc;
  }, {});

  const displayedPositions = Object.values(groupedPositions).map(p => {
    const isLong = p.direction === 'LONG';
    const entry = p.entry_price || 0;
    const current = p.current_price || entry;
    const shares = p.share_count || 0;
    const pnl = isLong ? (current - entry) * shares : (entry - current) * shares;
    const totalCost = entry * shares;
    const pnl_pct = totalCost > 0 ? pnl / totalCost : 0;
    return { ...p, pnl, pnl_pct };
  });

  const totalUnrealized = displayedPositions.reduce((s, p) => s + (p.pnl ?? 0), 0);

  const TABS = [
    ...(pending.length > 0 ? [{ key: 'pending', label: `Pending (${pending.length})` }] : []),
    { key: 'active', label: `Active (${displayedPositions.length})` },
    { key: 'closed', label: `Closed (${closed.length})` },
  ];

  const positionHeaders = ['Ticker', 'Shares', 'Entry', 'Current', 'P&L', 'Stop Loss', 'Size'];

  return (
    <div className="p-4 sm:p-6 space-y-5 max-w-7xl mx-auto">
      {confirm && (
        <ConfirmDialog
          title={confirm.action === 'approve' ? `Approve trade for ${confirm.ticker}?` : `Reject trade for ${confirm.ticker}?`}
          message={confirm.action === 'approve'
            ? 'This will send the order to the execution engine.'
            : 'This will reject the sizing recommendation.'}
          confirmLabel={confirm.action === 'approve' ? 'Yes, Approve' : 'Yes, Reject'}
          destructive={confirm.action === 'reject'}
          onConfirm={handleConfirm}
          onCancel={() => setConfirm(null)}
        />
      )}

      {/* Summary Strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: 'Open Positions', value: positions.length, mono: true },
          { label: 'Pending Approval', value: pending.length, mono: true },
          { label: 'Gross Exposure', value: exposure?.gross_exposure_pct != null ? `${exposure.gross_exposure_pct.toFixed(1)}%` : '—', mono: true },
          { label: 'Unrealized P&L', value: fmt$(totalUnrealized), mono: true, pnl: totalUnrealized },
        ].map(({ label, value, mono, pnl }) => (
          <div key={label} className="rounded-xl p-4" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
            <SL>{label}</SL>
            <div
              className="text-2xl font-bold mt-1"
              style={{
                fontFamily: mono ? 'JetBrains Mono' : 'Syne',
                color: pnl != null
                  ? pnl >= 0 ? 'var(--green)' : 'var(--red)'
                  : 'var(--text)',
              }}
            >
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* Exposure Bar */}
      {exposure && (
        <ExposureBar
          grossPct={exposure.gross_exposure_pct}
          netPct={exposure.net_exposure_pct}
          regime={regime?.regime}
        />
      )}

      {/* Tabs */}
      <div className="rounded-xl" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
        <div className="flex overflow-x-auto" style={{ borderBottom: '1px solid var(--border)' }}>
          {TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              style={{
                padding: '12px 20px',
                fontSize: '13px',
                fontWeight: 600,
                fontFamily: 'Syne',
                background: 'transparent',
                border: 'none',
                borderBottom: tab === t.key ? '2px solid var(--accent)' : '2px solid transparent',
                color: tab === t.key ? 'var(--accent)' : 'var(--text-3)',
                cursor: 'pointer',
                transition: 'color 0.15s',
                marginBottom: '-1px',
              }}
              onMouseEnter={e => { if (tab !== t.key) e.currentTarget.style.color = 'var(--text-2)'; }}
              onMouseLeave={e => { if (tab !== t.key) e.currentTarget.style.color = 'var(--text-3)'; }}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="p-5">
          {tab === 'pending' && (
            <div className="space-y-4">
              {pending.map(item => (
                <PendingCard
                  key={item.id}
                  item={item}
                  regime={regime}
                  isAdmin={isAdmin}
                  onApprove={(id) => setConfirm({ action: 'approve', id, ticker: item.ticker })}
                  onReject={(id) => setConfirm({ action: 'reject', id, ticker: item.ticker })}
                />
              ))}
            </div>
          )}

          {tab === 'active' && (
            <>
              <div className="flex justify-end mb-3">
                <button
                  onClick={() => exportCSV(displayedPositions, 'positions.csv')}
                  style={{
                    fontSize: '12px',
                    padding: '6px 12px',
                    background: 'var(--surface-2)',
                    color: 'var(--text-2)',
                    border: '1px solid var(--border)',
                    borderRadius: '8px',
                    cursor: 'pointer',
                    fontFamily: 'Syne',
                    fontWeight: 600,
                  }}
                >
                  Export CSV
                </button>
              </div>
              {displayedPositions.length === 0 ? (
                <p className="text-sm text-center py-8" style={{ color: 'var(--text-3)' }}>No open positions</p>
              ) : (
                <div className="responsive-table-wrap">
                  <table className="responsive-table mobile-stack w-full text-left">
                    <thead>
                      <tr style={{ background: 'var(--surface-2)', color: 'var(--text-3)' }}>
                        {positionHeaders.map(h => (
                          <th key={h} className="px-4 py-2" style={{ fontSize: '11px', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', fontFamily: 'Syne' }}>
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {displayedPositions.map(p => <PositionRow key={p.id ?? p.ticker} position={p} />)}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}

          {tab === 'closed' && (
            <>
              <div className="flex justify-end mb-3">
                <button
                  onClick={() => exportCSV(closed, 'closed_trades.csv')}
                  style={{
                    fontSize: '12px',
                    padding: '6px 12px',
                    background: 'var(--surface-2)',
                    color: 'var(--text-2)',
                    border: '1px solid var(--border)',
                    borderRadius: '8px',
                    cursor: 'pointer',
                    fontFamily: 'Syne',
                    fontWeight: 600,
                  }}
                >
                  Export CSV
                </button>
              </div>
              {closed.length === 0 ? (
                <p className="text-sm text-center py-8" style={{ color: 'var(--text-3)' }}>No closed trades yet</p>
              ) : (
                <div className="responsive-table-wrap">
                  <table className="responsive-table mobile-stack w-full text-left">
                    <thead>
                      <tr style={{ background: 'var(--surface-2)', color: 'var(--text-3)' }}>
                        {['Ticker', 'Direction', 'Shares', 'Entry', 'Exit', 'Realized P&L', 'Closed At'].map(h => (
                          <th key={h} className="px-4 py-2" style={{ fontSize: '11px', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', fontFamily: 'Syne' }}>
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {closed.map((p, i) => {
                        const pnlPos = (p.pnl ?? 0) >= 0;
                        return (
                          <tr
                            key={p.id ?? i}
                            style={{ borderBottom: '1px solid var(--border)', fontSize: '13px' }}
                            onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-2)'}
                            onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                          >
                            <td className="px-4 py-3" data-label="Ticker" style={{ fontFamily: 'JetBrains Mono', fontWeight: 700, color: 'var(--text)' }}>
                              {p.ticker}
                            </td>
                            <td className="px-4 py-3" data-label="Direction">
                              <span style={{
                                fontSize: '11px', fontWeight: 600, fontFamily: 'Syne',
                                padding: '2px 8px', borderRadius: '999px',
                                background: p.direction === 'LONG' ? 'var(--blue-bg)' : 'var(--red-bg)',
                                color: p.direction === 'LONG' ? 'var(--blue)' : 'var(--red)',
                                border: p.direction === 'LONG' ? '1px solid var(--blue-border)' : '1px solid var(--red-border)',
                              }}>
                                {p.direction}
                              </span>
                            </td>
                            <td className="px-4 py-3" data-label="Shares" style={{ fontFamily: 'JetBrains Mono', color: 'var(--text-2)' }}>
                              {p.share_count?.toLocaleString()}
                            </td>
                            <td className="px-4 py-3" data-label="Entry" style={{ fontFamily: 'JetBrains Mono', color: 'var(--text-2)' }}>
                              ${p.entry_price?.toFixed(2)}
                            </td>
                            <td className="px-4 py-3" data-label="Exit" style={{ fontFamily: 'JetBrains Mono', color: 'var(--text-2)' }}>
                              ${p.exit_price?.toFixed(2) ?? '—'}
                            </td>
                            <td className="px-4 py-3" data-label="Realized P&L" style={{ fontFamily: 'JetBrains Mono', fontWeight: 600, color: pnlPos ? 'var(--green)' : 'var(--red)' }}>
                              {p.pnl != null ? `${pnlPos ? '+' : ''}$${p.pnl.toFixed(2)}` : '—'}
                            </td>
                            <td className="px-4 py-3" data-label="Closed At" style={{ fontSize: '11px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
                              {p.closed_at ? new Date(p.closed_at).toLocaleDateString() : '—'}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
