import { useState, useEffect } from 'react';
import { useAuth } from '../context/AuthContext';
import { getWatchlist, runScreener } from '../api/screener';
import { triggerResearch } from '../api/research';
import SectorFilterTabs from '../components/SectorFilterTabs';

function SL({ children }) {
  return (
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
      {children}
    </div>
  );
}

const MSCORE_META = {
  CLEAN:             { bg: 'var(--green-bg)',  color: 'var(--green)',  border: 'var(--green-border)',  label: 'Clean' },
  FLAGGED:           { bg: 'var(--amber-bg)',  color: 'var(--amber)',  border: 'var(--amber-border)',  label: 'Flagged' },
  EXCLUDED:          { bg: 'var(--red-bg)',    color: 'var(--red)',    border: 'var(--red-border)',    label: 'Excluded' },
  INSUFFICIENT_DATA: { bg: 'var(--surface-2)', color: 'var(--text-3)', border: 'var(--border)',        label: 'No Data' },
};

const RAW_FACTOR_LABELS = {
  gross_profit_margin: 'Gross Profit Margin',
  revenue_growth: 'Revenue Growth',
  fcf_yield: 'Free Cash Flow Yield',
  debt_to_equity: 'Debt-to-Equity Ratio',
  current_ratio: 'Current Ratio (Liquidity)',
  roa: 'Return on Assets',
  ev_to_ebitda: 'EV / EBITDA',
  price_to_book: 'Price-to-Book',
  price_to_earnings: 'Price-to-Earnings (P/E)',
  momentum_3m: '3-Month Price Momentum',
  momentum_6m: '6-Month Price Momentum',
  momentum_12m: '12-Month Price Momentum',
  short_interest_pct: 'Short Interest %',
  insider_buy_score: 'Insider Buying Signal',
};

function fmtScore(v) {
  if (v == null) return '—';
  return typeof v === 'number' ? v.toFixed(2) : String(v);
}

function scoreColor(v) {
  if (v == null) return 'var(--text-3)';
  if (v >= 7) return 'var(--green)';
  if (v >= 5) return 'var(--amber)';
  return 'var(--red)';
}

export default function Screener() {
  const { isAdmin } = useAuth();
  const [watchlist, setWatchlist] = useState([]);
  const [lastRun, setLastRun] = useState(null);
  const [running, setRunning] = useState(false);
  const [sector, setSector] = useState('All');
  const [expanded, setExpanded] = useState(null);
  const [queuedTickers, setQueuedTickers] = useState(new Set());
  const [infoOpen, setInfoOpen] = useState(false);
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      const data = await getWatchlist(true); // allTime = true
      const items = Array.isArray(data) ? data : (data?.watchlist ?? []);
      setWatchlist(items);
      if (items.length) setLastRun(items[0]?.created_at ?? new Date().toISOString());
      setError(null);
    } catch (e) {
      setError('Could not load watchlist.');
    }
  };

  useEffect(() => { load(); }, []);

  const handleRun = async () => {
    setRunning(true);
    setError(null);
    try {
      await runScreener();
      await load();
    } catch {
      setError('Screener run failed. Check that the backend is running.');
    }
    setRunning(false);
  };

  const handleQueue = async (ticker) => {
    setQueuedTickers(prev => new Set([...prev, ticker]));
    try { await triggerResearch(ticker); } catch {}
  };

  const sectors = [...new Set(watchlist.map(w => w.sector).filter(Boolean))];
  const filtered = sector === 'All' ? watchlist : watchlist.filter(w => w.sector === sector);

  const fmtTime = (ts) => {
    if (!ts) return 'Never';
    try {
      const d = new Date(ts);
      return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch { return ts; }
  };

  return (
    <div className="p-6 space-y-5 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 style={{ color: 'var(--text)', fontFamily: 'Syne', fontWeight: 700 }} className="text-xl">
            Top 50 Tickers
          </h1>
          <p className="text-sm mt-0.5" style={{ color: 'var(--text-3)' }}>
            Top performers across all screening runs
          </p>
        </div>
        <button
          onClick={handleRun}
          disabled={running || !isAdmin}
          title={!isAdmin ? 'Guest mode — read only' : undefined}
          style={{
            background: 'var(--accent)',
            color: '#000',
            fontFamily: 'Syne',
            opacity: running || !isAdmin ? 0.7 : 1,
          }}
          className="px-4 py-2 text-sm font-medium rounded-lg disabled:cursor-not-allowed transition-colors flex items-center gap-2"
        >
          {running && (
            <span
              className="inline-block w-4 h-4 rounded-full animate-spin"
              style={{ border: '2px solid rgba(0,0,0,0.2)', borderTopColor: '#000' }}
            />
          )}
          {running ? 'Running… (30–60s)' : 'Run Screener'}
        </button>
      </div>

      {/* Error */}
      {error && (
        <div
          className="text-sm rounded-lg px-4 py-3"
          style={{
            background: 'var(--red-bg)',
            border: '1px solid var(--red-border)',
            color: 'var(--red)',
          }}
        >
          {error}
        </div>
      )}

      {/* M-Score Legend */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium mr-1" style={{ color: 'var(--text-3)' }}>Earnings Quality:</span>
        {Object.entries(MSCORE_META).map(([k, m]) => (
          k !== 'INSUFFICIENT_DATA' && (
            <span
              key={k}
              className="text-xs px-2.5 py-1 rounded-full font-medium cursor-help"
              style={{
                background: m.bg,
                color: m.color,
                border: `1px solid ${m.border}`,
              }}
              title={
                k === 'CLEAN' ? 'No earnings manipulation signals detected. Safe to trade.' :
                k === 'FLAGGED' ? 'Beneish M-score indicates possible earnings manipulation risk. Trade with caution.' :
                'High fraud risk detected. Excluded from trading universe.'
              }
            >
              {m.label}
            </span>
          )
        ))}
        <button
          onClick={() => setInfoOpen(v => !v)}
          className="text-xs hover:underline ml-1"
          style={{ color: 'var(--accent)' }}
        >
          What is M-score?
        </button>
        {infoOpen && (
          <div
            className="w-full rounded-lg p-3 text-xs leading-relaxed"
            style={{
              background: 'var(--blue-bg)',
              border: '1px solid var(--blue-border)',
              color: 'var(--blue)',
            }}
          >
            The <strong>Beneish M-score</strong> is a statistical model that detects earnings manipulation by analysing 8 financial ratios.
            A score above −1.78 suggests a high probability of manipulation (EXCLUDED).
            A score above −2.22 is a caution signal (FLAGGED).
            Below −2.22 is considered clean.
          </div>
        )}
      </div>

      {/* Sector Filter */}
      {sectors.length > 0 && (
        <SectorFilterTabs sectors={sectors} selected={sector} onChange={setSector} />
      )}

      {/* Table */}
      {filtered.length === 0 ? (
        <div className="text-center py-12" style={{ color: 'var(--text-3)' }}>
          <p className="text-sm">
            {watchlist.length === 0
              ? 'No stocks in watchlist yet. Run the screener to populate.'
              : 'No stocks match this filter.'}
          </p>
        </div>
      ) : (
        <div
          style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: '12px',
            overflow: 'hidden',
          }}
        >
          <div className="responsive-table-wrap">
            <table className="responsive-table mobile-stack w-full text-left">
              <thead style={{ background: 'var(--surface-2)' }}>
                <tr className="text-xs uppercase tracking-wide">
                  <th
                    className="px-4 py-3 font-medium"
                    style={{ color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700 }}
                  >Rank</th>
                  <th
                    className="px-4 py-3 font-medium"
                    style={{ color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700 }}
                  >Ticker</th>
                  <th
                    className="px-4 py-3 font-medium"
                    style={{ color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700 }}
                  >Composite Score</th>
                  <th
                    className="px-4 py-3 font-medium"
                    style={{ color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700 }}
                  >Quality</th>
                  <th
                    className="px-4 py-3 font-medium"
                    style={{ color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700 }}
                  >Value</th>
                  <th
                    className="px-4 py-3 font-medium"
                    style={{ color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700 }}
                  >Momentum</th>
                  <th
                    className="px-4 py-3 font-medium"
                    style={{ color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700 }}
                  >Earnings Quality</th>
                  <th
                    className="px-4 py-3 font-medium"
                    style={{ color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700 }}
                  >Research Status</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((item, idx) => {
                  const isExpanded = expanded === item.ticker;
                  const mscore = MSCORE_META[item.beneish_flag] || MSCORE_META.INSUFFICIENT_DATA;
                  const raw = item.raw_factors || {};
                  return [
                    <tr
                      key={item.ticker}
                      onClick={() => setExpanded(isExpanded ? null : item.ticker)}
                      style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer' }}
                      onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-2)'; }}
                      onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
                    >
                      <td className="px-4 py-3 text-sm font-medium" data-label="Rank" style={{ color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
                        #{idx + 1}
                      </td>
                      <td className="px-4 py-3" data-label="Ticker">
                        <div style={{ fontFamily: 'JetBrains Mono', color: 'var(--text)', fontWeight: 700 }}>{item.ticker}</div>
                        {item.sector && <div className="text-xs" style={{ color: 'var(--text-3)' }}>{item.sector}</div>}
                      </td>
                      <td className="px-4 py-3" data-label="Composite Score">
                        <span style={{ fontSize: '1.125rem', fontFamily: 'JetBrains Mono', fontWeight: 700, color: scoreColor(item.composite_score) }}>
                          {fmtScore(item.composite_score)}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm" data-label="Quality" style={{ color: scoreColor(item.quality_score), fontFamily: 'JetBrains Mono' }}>
                        {fmtScore(item.quality_score)}
                      </td>
                      <td className="px-4 py-3 text-sm" data-label="Value" style={{ color: scoreColor(item.value_score), fontFamily: 'JetBrains Mono' }}>
                        {fmtScore(item.value_score)}
                      </td>
                      <td className="px-4 py-3 text-sm" data-label="Momentum" style={{ color: scoreColor(item.momentum_score), fontFamily: 'JetBrains Mono' }}>
                        {fmtScore(item.momentum_score)}
                      </td>
                      <td className="px-4 py-3" data-label="Earnings Quality">
                        <span
                          className="text-xs px-2 py-0.5 rounded-full font-medium"
                          style={{
                            background: mscore.bg,
                            color: mscore.color,
                            border: `1px solid ${mscore.border}`,
                          }}
                        >
                          {mscore.label}
                        </span>
                      </td>
                      <td className="px-4 py-3" data-label="Research Status">
                        {item.queued_for_research ? (
                          <span
                            className="text-xs px-3 py-1.5 rounded-lg font-medium"
                            style={{
                              background: 'var(--green-bg)',
                              border: '1px solid var(--green-border)',
                              color: 'var(--green)',
                            }}
                          >
                            Queued ✓
                          </span>
                        ) : item.beneish_flag !== 'EXCLUDED' ? (
                          <button
                            onClick={(e) => { e.stopPropagation(); handleQueue(item.ticker); }}
                            disabled={queuedTickers.has(item.ticker) || !isAdmin}
                            title={!isAdmin ? 'Guest mode — read only' : undefined}
                            className="text-xs px-3 py-1.5 rounded-lg transition-colors font-medium"
                            style={
                              queuedTickers.has(item.ticker)
                                ? {
                                    background: 'var(--surface-2)',
                                    color: 'var(--text-3)',
                                    cursor: 'default',
                                    border: '1px solid var(--border)',
                                    fontFamily: 'Syne',
                                  }
                                : !isAdmin
                                ? {
                                    background: 'var(--surface-2)',
                                    color: 'var(--text-3)',
                                    cursor: 'not-allowed',
                                    border: '1px solid var(--border)',
                                    fontFamily: 'Syne',
                                    opacity: 0.4,
                                  }
                                : {
                                    background: 'var(--accent-muted)',
                                    border: '1px solid var(--accent-ring)',
                                    color: 'var(--accent)',
                                    fontFamily: 'Syne',
                                  }
                            }
                          >
                            {queuedTickers.has(item.ticker) ? 'Queued ✓' : 'Queue Research'}
                          </button>
                        ) : null}
                      </td>
                    </tr>,
                    isExpanded && Object.keys(raw).length > 0 && (
                      <tr key={`${item.ticker}-expanded`} className="stack-full-row">
                        <td colSpan={8} className="px-6 py-4" style={{ background: 'var(--surface-2)' }}>
                          <SL>Raw Factor Detail — {item.ticker}</SL>
                          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-x-8 gap-y-1.5 mt-3">
                            {Object.entries(raw).flatMap(([group, v]) =>
                              v && typeof v === 'object' && !Array.isArray(v)
                                ? Object.entries(v).map(([k, val]) => ({ key: k, val, group }))
                                : [{ key: group, val: v, group }]
                            ).map(({ key, val }) => (
                              <div
                                key={key}
                                className="flex justify-between text-xs pb-1"
                                style={{ borderBottom: '1px solid var(--border)' }}
                              >
                                <span style={{ color: 'var(--text-3)' }}>{RAW_FACTOR_LABELS[key] || key}</span>
                                <span className="ml-2" style={{ color: 'var(--text-2)', fontFamily: 'JetBrains Mono' }}>
                                  {val == null ? '—' : typeof val === 'number' ? val.toFixed(3) : String(val)}
                                </span>
                              </div>
                            ))}
                          </div>
                        </td>
                      </tr>
                    ),
                  ];
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
