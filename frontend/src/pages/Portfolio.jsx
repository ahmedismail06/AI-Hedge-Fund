import { useState, useEffect, useCallback } from 'react';
import { getPositions, getPending, getExposure, getHistory, approveTrade, rejectTrade } from '../api/portfolio';
import { getRegime } from '../api/macro';
import ExposureBar, { REGIME_CAPS } from '../components/ExposureBar';
import PositionRow from '../components/PositionRow';
import ConfirmDialog from '../components/ConfirmDialog';

const STOP_TIERS = {
  normal:  { tier1: -8,  tier2: -15, tier3: -20 },
  riskOff: { tier1: -5,  tier2: -10, tier3: -15 },
};

const VERDICT_COLORS = {
  LONG:  'bg-green-100 text-green-700',
  SHORT: 'bg-red-100 text-red-700',
  AVOID: 'bg-gray-100 text-gray-600',
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

function PendingCard({ item, regime, onApprove, onReject }) {
  const isRiskOff = regime && (regime.regime === 'Risk-Off' || regime.regime === 'Stagflation');
  const tiers = isRiskOff ? STOP_TIERS.riskOff : STOP_TIERS.normal;
  const cap = REGIME_CAPS[regime?.regime] ?? 100;

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
      <div className="flex items-start justify-between gap-4 mb-4">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono font-bold text-xl text-gray-900">{item.ticker}</span>
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${VERDICT_COLORS[item.verdict] || 'bg-gray-100 text-gray-600'}`}>
              {item.verdict}
            </span>
            <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
              item.conviction_score >= 8 ? 'bg-green-100 text-green-700' :
              item.conviction_score >= 6 ? 'bg-yellow-100 text-yellow-700' :
              'bg-red-100 text-red-700'
            }`}>
              Conviction {item.conviction_score?.toFixed(1)}/10
            </span>
          </div>
          {item.size_label && (
            <p className="text-sm text-gray-600 mt-1">
              <strong>{item.size_label} position</strong> — {
                { Large: '8%', Medium: '5%', Small: '2%', Micro: '1%' }[item.size_label] ?? ''
              } of portfolio
              {item.portfolio_value ? ` (~${fmt$(item.portfolio_value * ({ Large: 0.08, Medium: 0.05, Small: 0.02, Micro: 0.01 }[item.size_label] ?? 0))})` : ''}
            </p>
          )}
        </div>
        <div className="flex gap-2 flex-shrink-0">
          <button
            onClick={() => onApprove(item.id)}
            className="px-4 py-2 text-sm font-medium bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors"
          >
            Approve
          </button>
          <button
            onClick={() => onReject(item.id)}
            className="px-4 py-2 text-sm font-medium bg-red-50 text-red-600 border border-red-200 rounded-lg hover:bg-red-100 transition-colors"
          >
            Reject
          </button>
        </div>
      </div>

      {/* Stop-loss ladder */}
      <div className="bg-gray-50 rounded-lg p-3 space-y-2">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Stop-Loss Protection {isRiskOff && <span className="text-yellow-600">(Tighter — {regime.regime} Mode)</span>}
        </p>
        {[
          { label: 'Tier 1 — Position Stop', pct: tiers.tier1, desc: 'Auto-sell this position if price falls this much from entry' },
          { label: 'Tier 2 — Strategy Stop', pct: tiers.tier2, desc: 'Close all strategy positions if strategy P&L hits this level' },
          { label: 'Tier 3 — Portfolio Stop', pct: tiers.tier3, desc: 'Halt all trading if portfolio drawdown reaches this level' },
        ].map(({ label, pct, desc }) => (
          <div key={label} className="flex items-center gap-3 text-sm">
            <span className="w-40 text-xs font-medium text-gray-600 flex-shrink-0">{label}</span>
            <span className="font-bold text-red-600 w-10 text-right">{pct}%</span>
            <span className="text-xs text-gray-400">{desc}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Portfolio() {
  const [tab, setTab] = useState('pending');
  const [pending, setPending] = useState([]);
  const [positions, setPositions] = useState([]);
  const [closed, setClosed] = useState([]);
  const [exposure, setExposure] = useState(null);
  const [regime, setRegime] = useState(null);
  const [confirm, setConfirm] = useState(null); // { action: 'approve'|'reject', id, ticker }

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
    // Optimistic removal
    setPending(prev => prev.filter(p => p.id !== id));
    setConfirm(null);
    try {
      if (action === 'approve') await approveTrade(id);
      else await rejectTrade(id);
    } catch {
      // Re-load if it fails
      loadPending();
    }
  };

  const totalUnrealized = positions.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0);

  const TABS = [
    { key: 'pending', label: `Pending (${pending.length})` },
    { key: 'active', label: `Active (${positions.length})` },
    { key: 'closed', label: `Closed (${closed.length})` },
  ];

  const positionHeaders = ['Ticker', 'Shares', 'Entry', 'Current', 'P&L', 'Stop Loss', 'Size'];

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      {confirm && (
        <ConfirmDialog
          title={confirm.action === 'approve' ? `Approve trade for ${confirm.ticker}?` : `Reject trade for ${confirm.ticker}?`}
          message={confirm.action === 'approve'
            ? 'This will send the order to the execution engine. Make sure you have reviewed the sizing and stop-loss levels.'
            : 'This will mark the recommendation as rejected. You can re-run research to generate a new recommendation.'}
          confirmLabel={confirm.action === 'approve' ? 'Yes, Approve' : 'Yes, Reject'}
          destructive={confirm.action === 'reject'}
          onConfirm={handleConfirm}
          onCancel={() => setConfirm(null)}
        />
      )}

      {/* Summary Strip */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: 'Open Positions', value: positions.length },
          { label: 'Pending Approval', value: pending.length },
          { label: 'Gross Exposure', value: exposure?.gross_exposure_pct != null ? `${exposure.gross_exposure_pct.toFixed(1)}%` : '—' },
          { label: 'Unrealized P&L', value: fmt$(totalUnrealized) },
        ].map(({ label, value }) => (
          <div key={label} className="bg-white rounded-xl border border-gray-200 p-4">
            <div className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</div>
            <div className="text-2xl font-bold text-gray-900 mt-1">{value}</div>
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
      <div className="bg-white rounded-xl border border-gray-200">
        <div className="flex border-b border-gray-200">
          {TABS.map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-5 py-3 text-sm font-medium transition-colors ${
                tab === t.key
                  ? 'border-b-2 border-blue-600 text-blue-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="p-5">
          {tab === 'pending' && (
            pending.length === 0 ? (
              <div className="text-center py-8 text-gray-400">
                <p className="text-sm">No trades awaiting approval.</p>
                <p className="text-xs mt-1">The system will surface new recommendations after the next screening cycle.</p>
              </div>
            ) : (
              <div className="space-y-4">
                {pending.map(item => (
                  <PendingCard
                    key={item.id}
                    item={item}
                    regime={regime}
                    onApprove={(id) => setConfirm({ action: 'approve', id, ticker: item.ticker })}
                    onReject={(id) => setConfirm({ action: 'reject', id, ticker: item.ticker })}
                  />
                ))}
              </div>
            )
          )}

          {tab === 'active' && (
            <>
              <div className="flex justify-end mb-3">
                <button
                  onClick={() => exportCSV(positions, 'positions.csv')}
                  className="text-xs px-3 py-1.5 border border-gray-300 rounded-lg text-gray-600 hover:bg-gray-50 transition-colors"
                >
                  Export CSV
                </button>
              </div>
              {positions.length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-8">No open positions</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="text-xs text-gray-500 uppercase tracking-wide border-b border-gray-100">
                        {positionHeaders.map(h => <th key={h} className="px-4 py-2 font-medium">{h}</th>)}
                      </tr>
                    </thead>
                    <tbody>
                      {positions.map(p => <PositionRow key={p.id ?? p.ticker} position={p} />)}
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
                  className="text-xs px-3 py-1.5 border border-gray-300 rounded-lg text-gray-600 hover:bg-gray-50 transition-colors"
                >
                  Export CSV
                </button>
              </div>
              {closed.length === 0 ? (
                <p className="text-sm text-gray-400 text-center py-8">No closed trades yet</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="text-xs text-gray-500 uppercase tracking-wide border-b border-gray-100">
                        {['Ticker', 'Direction', 'Shares', 'Entry', 'Exit', 'Realized P&L', 'Closed At'].map(h => (
                          <th key={h} className="px-4 py-2 font-medium">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {closed.map((p, i) => {
                        const pnlPos = (p.realized_pnl ?? 0) >= 0;
                        return (
                          <tr key={p.id ?? i} className="border-b border-gray-100 text-sm">
                            <td className="px-4 py-3 font-mono font-bold">{p.ticker}</td>
                            <td className="px-4 py-3">
                              <span className={`text-xs px-1.5 py-0.5 rounded ${p.direction === 'LONG' ? 'bg-blue-100 text-blue-700' : 'bg-red-100 text-red-700'}`}>
                                {p.direction}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-gray-700">{p.shares?.toLocaleString()}</td>
                            <td className="px-4 py-3 text-gray-700">${p.entry_price?.toFixed(2)}</td>
                            <td className="px-4 py-3 text-gray-700">${p.exit_price?.toFixed(2) ?? '—'}</td>
                            <td className={`px-4 py-3 font-medium ${pnlPos ? 'text-green-600' : 'text-red-600'}`}>
                              {p.realized_pnl != null ? `${pnlPos ? '+' : ''}$${p.realized_pnl.toFixed(2)}` : '—'}
                            </td>
                            <td className="px-4 py-3 text-gray-400 text-xs">
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
