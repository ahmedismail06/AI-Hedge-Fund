import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { getPositions, getPending, getExposure, getHistory, approveTrade, rejectTrade } from '../api/portfolio';
import { getRegime } from '../api/macro';
import PositionTableFull from '../components/PositionTableFull';
import SectorBreakdownChart from '../components/SectorBreakdownChart';
import TickerDrawer from '../components/TickerDrawer';
import ConfirmDialog from '../components/ConfirmDialog';
import ConvictionBadge from '../components/ConvictionBadge';

const fmt$ = (v) => {
  if (v == null) return '—';
  const abs = Math.abs(v);
  const prefix = v < 0 ? '-' : '';
  if (abs >= 1_000_000) return `${prefix}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000)     return `${prefix}$${(abs / 1_000).toFixed(1)}k`;
  return `${prefix}$${abs.toFixed(2)}`;
};

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

function Panel({ children, style = {} }) {
  return (
    <div className="panel" style={style}>
      {children}
    </div>
  );
}

function PanelHeader({ label, title, action, onAction }) {
  return (
    <div className="panel-header">
      <div>
        <SL>{label}</SL>
        {title && <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text)', marginTop: '1px' }}>{title}</div>}
      </div>
      {action && (
        <button onClick={onAction} style={{ fontSize: '10px', fontWeight: 700, color: 'var(--accent)', fontFamily: 'Syne', background: 'none', border: 'none', cursor: 'pointer' }}>
          {action}
        </button>
      )}
    </div>
  );
}

function StatCard({ label, value, valueColor }) {
  return (
    <div className="panel-compact px-4 py-3">
      <SL>{label}</SL>
      <div style={{ fontFamily: 'JetBrains Mono', fontSize: '20px', fontWeight: 800, color: valueColor ?? 'var(--text)', marginTop: '3px', lineHeight: 1 }}>
        {value}
      </div>
    </div>
  );
}

const STOP_TIERS = {
  normal:  { tier1: -8,  tier2: -15, tier3: -20 },
  riskOff: { tier1: -5,  tier2: -10, tier3: -15 },
};

function PendingCard({ item, regime, onApprove, onReject, isAdmin }) {
  const isRiskOff = regime && (regime.regime === 'Risk-Off' || regime.regime === 'Stagflation');
  const tiers = isRiskOff ? STOP_TIERS.riskOff : STOP_TIERS.normal;

  return (
    <div className="rounded-xl p-4" style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}>
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span style={{ fontFamily: 'JetBrains Mono', fontWeight: 700, fontSize: '16px', color: 'var(--text)' }}>{item.ticker}</span>
            <span style={{
              fontSize: '10px', fontWeight: 700, fontFamily: 'Syne', padding: '2px 6px', borderRadius: '4px',
              background: item.verdict === 'LONG' ? 'var(--green-bg)' : item.verdict === 'SHORT' ? 'var(--red-bg)' : 'var(--surface)',
              color: item.verdict === 'LONG' ? 'var(--green)' : item.verdict === 'SHORT' ? 'var(--red)' : 'var(--text-3)',
              border: `1px solid ${item.verdict === 'LONG' ? 'var(--green-border)' : item.verdict === 'SHORT' ? 'var(--red-border)' : 'var(--border)'}`,
            }}>
              {item.verdict}
            </span>
            <ConvictionBadge score={item.conviction_score} />
          </div>
          {item.size_label && (
            <p style={{ fontSize: '11px', color: 'var(--text-2)', marginTop: '3px' }}>
              {item.size_label} position — {({ Large: '8%', Medium: '5%', Small: '2%', Micro: '1%' })[item.size_label] ?? ''} of portfolio
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => onApprove(item.id)}
            disabled={!isAdmin}
            style={{
              padding: '5px 12px', fontSize: '11px', fontWeight: 700, fontFamily: 'Syne',
              background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-border)',
              borderRadius: '6px', cursor: isAdmin ? 'pointer' : 'not-allowed', opacity: !isAdmin ? 0.4 : 1,
            }}
          >
            Approve
          </button>
          <button
            onClick={() => onReject(item.id)}
            disabled={!isAdmin}
            style={{
              padding: '5px 12px', fontSize: '11px', fontWeight: 700, fontFamily: 'Syne',
              background: 'var(--surface)', color: 'var(--text-2)', border: '1px solid var(--border)',
              borderRadius: '6px', cursor: isAdmin ? 'pointer' : 'not-allowed', opacity: !isAdmin ? 0.4 : 1,
            }}
          >
            Reject
          </button>
        </div>
      </div>

      {/* Stop ladder */}
      <div className="rounded-lg p-3" style={{ background: 'var(--surface-3)' }}>
        <SL>Stop-Loss Tiers {isRiskOff ? '— Risk-Off Mode' : ''}</SL>
        <div className="space-y-1 mt-1.5">
          {[
            { label: 'Tier 1 — Position', pct: tiers.tier1 },
            { label: 'Tier 2 — Strategy', pct: tiers.tier2 },
            { label: 'Tier 3 — Portfolio', pct: tiers.tier3 },
          ].map(({ label, pct }) => (
            <div key={label} className="flex items-center gap-3">
              <span style={{ fontSize: '10px', color: 'var(--text-3)', fontFamily: 'Syne', width: '120px', flexShrink: 0 }}>{label}</span>
              <span style={{ fontFamily: 'JetBrains Mono', fontSize: '11px', fontWeight: 700, color: 'var(--red)' }}>{pct}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export default function Book() {
  const { isAdmin } = useAuth();
  const [tab,       setTab]       = useState('active');
  const [positions, setPositions] = useState([]);
  const [pending,   setPending]   = useState([]);
  const [closed,    setClosed]    = useState([]);
  const [exposure,  setExposure]  = useState(null);
  const [regime,    setRegime]    = useState(null);
  const [selectedPosition, setSelectedPosition] = useState(null);
  const [confirm,   setConfirm]   = useState(null);

  const loadPending   = useCallback(async () => { try { setPending(await getPending()); } catch {} }, []);
  const loadPositions = useCallback(async () => { try { setPositions(await getPositions()); } catch {} }, []);

  useEffect(() => {
    const init = async () => {
      try {
        const [exp, reg, hist] = await Promise.all([getExposure(), getRegime(), getHistory()]);
        setExposure(exp);
        setRegime(reg);
        setClosed(Array.isArray(hist) ? hist : []);
      } catch {}
    };
    init(); loadPending(); loadPositions();
    const t1 = setInterval(loadPending,   30_000);
    const t2 = setInterval(loadPositions, 60_000);
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

  const grouped = positions.reduce((acc, p) => {
    if (!acc[p.ticker]) { acc[p.ticker] = { ...p }; }
    else {
      const ex = acc[p.ticker];
      const total = (ex.share_count || 0) + (p.share_count || 0);
      if (total > 0) ex.entry_price = ((ex.entry_price || 0) * (ex.share_count || 0) + (p.entry_price || 0) * (p.share_count || 0)) / total;
      ex.share_count = total;
    }
    return acc;
  }, {});
  const displayPositions = Object.values(grouped);

  const totalPnl = displayPositions.reduce((sum, p) => {
    const isLong = p.direction === 'LONG';
    const e = p.entry_price ?? 0;
    const c = p.current_price ?? e;
    const s = p.share_count ?? 0;
    return sum + (isLong ? (c - e) * s : (e - c) * s);
  }, 0);

  const longBook  = displayPositions.filter(p => p.direction === 'LONG');
  const shortBook = displayPositions.filter(p => p.direction === 'SHORT');

  const TABS = [
    ...(pending.length > 0 ? [{ key: 'pending', label: `Pending (${pending.length})` }] : []),
    { key: 'active', label: `Active (${displayPositions.length})` },
    { key: 'closed', label: `Closed (${closed.length})` },
  ];

  return (
    <div className="p-4 animate-fade-in" style={{ maxWidth: '1600px', margin: '0 auto' }}>
      {confirm && (
        <ConfirmDialog
          title={confirm.action === 'approve' ? `Approve ${confirm.ticker}?` : `Reject ${confirm.ticker}?`}
          message={confirm.action === 'approve' ? 'Send order to execution engine.' : 'Reject this recommendation.'}
          confirmLabel={confirm.action === 'approve' ? 'Approve' : 'Reject'}
          destructive={confirm.action === 'reject'}
          onConfirm={handleConfirm}
          onCancel={() => setConfirm(null)}
        />
      )}

      {selectedPosition && (
        <TickerDrawer
          position={selectedPosition}
          onClose={() => setSelectedPosition(null)}
        />
      )}

      {/* Stat strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
        <StatCard label="Long Book"  value={`${longBook.length} positions`} />
        <StatCard label="Short Book" value={`${shortBook.length} positions`} />
        <StatCard
          label="Open P&L"
          value={fmt$(totalPnl)}
          valueColor={totalPnl >= 0 ? 'var(--green)' : 'var(--red)'}
        />
        <StatCard
          label="Gross / Net"
          value={exposure ? `${exposure.gross_exposure_pct?.toFixed(0)}% / ${exposure.net_exposure_pct?.toFixed(0)}%` : '—'}
          valueColor={exposure?.gross_exposure_pct > 150 ? 'var(--red)' : 'var(--text)'}
        />
      </div>

      {/* Main grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: '12px', alignItems: 'start' }}>

        {/* Left: position table */}
        <Panel>
          {/* Tabs */}
          <div className="flex overflow-x-auto no-scrollbar" style={{ borderBottom: '1px solid var(--border)' }}>
            {TABS.map(t => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                style={{
                  padding: '10px 16px',
                  fontSize: '11px', fontWeight: 700, fontFamily: 'Syne',
                  background: 'transparent', border: 'none',
                  borderBottom: tab === t.key ? '2px solid var(--accent)' : '2px solid transparent',
                  color: tab === t.key ? 'var(--accent)' : 'var(--text-3)',
                  cursor: 'pointer', marginBottom: '-1px', whiteSpace: 'nowrap',
                  transition: 'color 0.15s',
                }}
              >
                {t.label}
              </button>
            ))}

            {tab === 'active' && (
              <button
                onClick={() => exportCSV(displayPositions, 'positions.csv')}
                className="ml-auto"
                style={{
                  padding: '10px 14px', fontSize: '10px', fontWeight: 600, fontFamily: 'Syne',
                  background: 'none', border: 'none', color: 'var(--text-3)', cursor: 'pointer',
                }}
              >
                Export CSV
              </button>
            )}
          </div>

          <div className="p-4">
            {tab === 'pending' && (
              <div className="space-y-3">
                {pending.map(item => (
                  <PendingCard
                    key={item.id}
                    item={item}
                    regime={regime}
                    isAdmin={isAdmin}
                    onApprove={id => setConfirm({ action: 'approve', id, ticker: item.ticker })}
                    onReject={id => setConfirm({ action: 'reject', id, ticker: item.ticker })}
                  />
                ))}
              </div>
            )}

            {tab === 'active' && (
              <PositionTableFull positions={displayPositions} onTickerClick={setSelectedPosition} />
            )}

            {tab === 'closed' && (
              <div>
                <div className="flex justify-end mb-3">
                  <button
                    onClick={() => exportCSV(closed, 'closed_trades.csv')}
                    style={{ fontSize: '11px', padding: '5px 10px', background: 'var(--surface-2)', color: 'var(--text-2)', border: '1px solid var(--border)', borderRadius: '6px', cursor: 'pointer', fontFamily: 'Syne' }}
                  >
                    Export CSV
                  </button>
                </div>
                {closed.length === 0 ? (
                  <p className="text-center py-8" style={{ fontSize: '13px', color: 'var(--text-3)' }}>No closed trades yet</p>
                ) : (
                  <div className="responsive-table-wrap">
                    <table className="responsive-table w-full text-left" style={{ borderCollapse: 'collapse' }}>
                      <thead>
                        <tr style={{ background: 'var(--surface-2)' }}>
                          {['Ticker', 'Direction', 'Shares', 'Entry', 'Exit', 'Realized P&L', 'Closed At'].map(h => (
                            <th key={h} className="px-4 py-2" style={{ fontSize: '10px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-3)', fontFamily: 'Syne' }}>
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
                              style={{ borderBottom: '1px solid var(--border)' }}
                              onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-2)'}
                              onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                            >
                              <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontWeight: 700, color: 'var(--text)' }}>{p.ticker}</td>
                              <td className="px-4 py-3">
                                <span style={{ fontSize: '10px', fontWeight: 700, fontFamily: 'Syne', padding: '1px 6px', borderRadius: '3px', background: p.direction === 'LONG' ? 'var(--blue-bg)' : 'var(--red-bg)', color: p.direction === 'LONG' ? 'var(--blue)' : 'var(--red)', border: `1px solid ${p.direction === 'LONG' ? 'var(--blue-border)' : 'var(--red-border)'}` }}>
                                  {p.direction}
                                </span>
                              </td>
                              <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', color: 'var(--text-2)' }}>{p.share_count?.toLocaleString()}</td>
                              <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', color: 'var(--text-2)' }}>${p.entry_price?.toFixed(2)}</td>
                              <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', color: 'var(--text-2)' }}>${p.exit_price?.toFixed(2) ?? '—'}</td>
                              <td className="px-4 py-3" style={{ fontFamily: 'JetBrains Mono', fontSize: '12px', fontWeight: 700, color: pnlPos ? 'var(--green)' : 'var(--red)' }}>
                                {p.pnl != null ? `${pnlPos ? '+' : ''}$${p.pnl.toFixed(2)}` : '—'}
                              </td>
                              <td className="px-4 py-3" style={{ fontSize: '11px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
                                {p.closed_at ? new Date(p.closed_at).toLocaleDateString() : '—'}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>
        </Panel>

        {/* Right: Exposure + Sector + Pending */}
        <div className="space-y-3">
          {/* Exposure summary */}
          {exposure && (
            <Panel>
              <div className="p-4">
                <SL>Exposure</SL>
                <div className="mt-3 space-y-3">
                  {[
                    { label: 'Gross', value: exposure.gross_exposure_pct, cap: 150, color: 'var(--blue)' },
                    { label: 'Net',   value: Math.abs(exposure.net_exposure_pct ?? 0), cap: 100, color: 'var(--accent)' },
                  ].map(({ label, value, cap, color }) => {
                    const pct = Math.min((value / cap) * 100, 100);
                    const isOver = value > cap;
                    return (
                      <div key={label}>
                        <div className="flex justify-between mb-1">
                          <span style={{ fontSize: '10px', color: 'var(--text-2)', fontFamily: 'Syne' }}>{label}</span>
                          <span style={{ fontSize: '11px', fontWeight: 700, fontFamily: 'JetBrains Mono', color: isOver ? 'var(--red)' : 'var(--text)' }}>
                            {value != null ? `${value.toFixed(1)}%` : '—'}
                          </span>
                        </div>
                        <div className="rounded-full overflow-hidden" style={{ height: '6px', background: 'var(--border)' }}>
                          <div style={{ width: `${pct}%`, height: '100%', background: isOver ? 'var(--red)' : color, transition: 'width 0.4s ease' }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
                {regime?.regime && (
                  <p style={{ fontSize: '9px', color: 'var(--text-3)', marginTop: '8px', fontFamily: 'Syne' }}>
                    Regime cap ({regime.regime}): {({ 'Risk-On': '150%', 'Risk-Off': '80%', 'Transitional': '120%', 'Stagflation': '100%' })[regime.regime] ?? '100%'}
                  </p>
                )}
              </div>
            </Panel>
          )}

          {/* Sector breakdown */}
          {displayPositions.length > 0 && (
            <Panel>
              <div className="p-4">
                <SL>Sector Breakdown</SL>
                <div className="mt-2">
                  <SectorBreakdownChart positions={displayPositions} height={Math.min(displayPositions.length * 22 + 30, 200)} />
                </div>
              </div>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}
