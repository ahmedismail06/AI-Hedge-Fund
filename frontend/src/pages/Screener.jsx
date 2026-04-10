import { useState, useEffect } from 'react';
import { getWatchlist, runScreener } from '../api/screener';
import { triggerResearch } from '../api/research';
import SectorFilterTabs from '../components/SectorFilterTabs';

const MSCORE_META = {
  CLEAN:              { color: 'bg-green-100 text-green-700 border-green-200', label: 'Clean' },
  FLAGGED:            { color: 'bg-yellow-100 text-yellow-700 border-yellow-200', label: 'Flagged' },
  EXCLUDED:           { color: 'bg-red-100 text-red-700 border-red-200', label: 'Excluded' },
  INSUFFICIENT_DATA:  { color: 'bg-gray-100 text-gray-600 border-gray-200', label: 'No Data' },
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
  if (v == null) return 'text-gray-400';
  if (v >= 7) return 'text-green-600 font-semibold';
  if (v >= 5) return 'text-yellow-600';
  return 'text-red-500';
}

export default function Screener() {
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
      const data = await getWatchlist();
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
          <h1 className="text-xl font-bold text-gray-900">Stock Screener</h1>
          <p className="text-sm text-gray-400 mt-0.5">Last run: {fmtTime(lastRun)}</p>
        </div>
        <button
          onClick={handleRun}
          disabled={running}
          className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors flex items-center gap-2"
        >
          {running && <span className="inline-block w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />}
          {running ? 'Running… (30–60s)' : 'Run Screener'}
        </button>
      </div>

      {error && (
        <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-3">{error}</div>
      )}

      {/* M-Score Legend */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-gray-500 mr-1">Earnings Quality:</span>
        {Object.entries(MSCORE_META).map(([k, m]) => (
          k !== 'INSUFFICIENT_DATA' && (
            <span key={k} className={`text-xs px-2.5 py-1 rounded-full border font-medium cursor-help ${m.color}`} title={
              k === 'CLEAN' ? 'No earnings manipulation signals detected. Safe to trade.' :
              k === 'FLAGGED' ? 'Beneish M-score indicates possible earnings manipulation risk. Trade with caution.' :
              'High fraud risk detected. Excluded from trading universe.'
            }>
              {m.label}
            </span>
          )
        ))}
        <button onClick={() => setInfoOpen(v => !v)} className="text-xs text-blue-500 hover:underline ml-1">
          What is M-score?
        </button>
        {infoOpen && (
          <div className="w-full bg-blue-50 border border-blue-100 rounded-lg p-3 text-xs text-blue-800 leading-relaxed">
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
        <div className="text-center py-12 text-gray-400">
          <p className="text-sm">{watchlist.length === 0 ? 'No stocks in watchlist yet. Run the screener to populate.' : 'No stocks match this filter.'}</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-left">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr className="text-xs text-gray-500 uppercase tracking-wide">
                <th className="px-4 py-3 font-medium">Rank</th>
                <th className="px-4 py-3 font-medium">Ticker</th>
                <th className="px-4 py-3 font-medium">Composite Score</th>
                <th className="px-4 py-3 font-medium">Quality</th>
                <th className="px-4 py-3 font-medium">Value</th>
                <th className="px-4 py-3 font-medium">Momentum</th>
                <th className="px-4 py-3 font-medium">Earnings Quality</th>
                <th className="px-4 py-3 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item, idx) => {
                const isExpanded = expanded === item.ticker;
                const mscore = MSCORE_META[item.beneish_status] || MSCORE_META.INSUFFICIENT_DATA;
                const raw = item.raw_factors || {};
                return [
                  <tr
                    key={item.ticker}
                    onClick={() => setExpanded(isExpanded ? null : item.ticker)}
                    className="border-b border-gray-100 hover:bg-gray-50 cursor-pointer transition-colors"
                  >
                    <td className="px-4 py-3 text-sm font-medium text-gray-500">#{item.rank ?? idx + 1}</td>
                    <td className="px-4 py-3">
                      <div className="font-mono font-bold text-gray-900">{item.ticker}</div>
                      {item.sector && <div className="text-xs text-gray-400">{item.sector}</div>}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`text-lg font-bold ${scoreColor(item.composite_score)}`}>
                        {fmtScore(item.composite_score)}
                      </span>
                    </td>
                    <td className={`px-4 py-3 text-sm ${scoreColor(item.quality_score)}`}>{fmtScore(item.quality_score)}</td>
                    <td className={`px-4 py-3 text-sm ${scoreColor(item.value_score)}`}>{fmtScore(item.value_score)}</td>
                    <td className={`px-4 py-3 text-sm ${scoreColor(item.momentum_score)}`}>{fmtScore(item.momentum_score)}</td>
                    <td className="px-4 py-3">
                      <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${mscore.color}`}>
                        {mscore.label}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {item.beneish_status !== 'EXCLUDED' && (
                        <button
                          onClick={(e) => { e.stopPropagation(); handleQueue(item.ticker); }}
                          disabled={queuedTickers.has(item.ticker)}
                          className={`text-xs px-3 py-1.5 rounded-lg transition-colors font-medium ${
                            queuedTickers.has(item.ticker)
                              ? 'bg-gray-100 text-gray-400 cursor-default'
                              : 'bg-blue-50 text-blue-600 hover:bg-blue-100 border border-blue-200'
                          }`}
                        >
                          {queuedTickers.has(item.ticker) ? 'Queued ✓' : 'Queue Research'}
                        </button>
                      )}
                    </td>
                  </tr>,
                  isExpanded && Object.keys(raw).length > 0 && (
                    <tr key={`${item.ticker}-expanded`} className="bg-blue-50 border-b border-gray-100">
                      <td colSpan={8} className="px-6 py-4">
                        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">Raw Factor Detail — {item.ticker}</p>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-x-8 gap-y-1.5">
                          {Object.entries(raw).flatMap(([group, v]) =>
                            v && typeof v === 'object' && !Array.isArray(v)
                              ? Object.entries(v).map(([k, val]) => ({ key: k, val, group }))
                              : [{ key: group, val: v, group }]
                          ).map(({ key, val }) => (
                            <div key={key} className="flex justify-between text-xs border-b border-blue-100 pb-1">
                              <span className="text-gray-500">{RAW_FACTOR_LABELS[key] || key}</span>
                              <span className="font-medium text-gray-700 ml-2">
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
      )}
    </div>
  );
}
