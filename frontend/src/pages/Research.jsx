// Research Page
// Ticker search → triggers memo generation → displays MemoCard
// Below: scrollable history list of past memos

import { useState, useEffect, useRef } from 'react';
import MemoCard from '../components/MemoCard';
import ConvictionBadge from '../components/ConvictionBadge';
import { triggerResearch, getHistory, getLatestMemo } from '../api/research';

const VERDICT_DOT = {
  LONG: 'bg-green-500',
  SHORT: 'bg-red-500',
  AVOID: 'bg-gray-400',
};

export default function Research() {
  const [tickerInput, setTickerInput] = useState('');
  const [activeMemo, setActiveMemo] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadingMemo, setLoadingMemo] = useState(false);
  const [error, setError] = useState(null);
  const inputRef = useRef(null);

  useEffect(() => {
    loadHistory();
    inputRef.current?.focus();
  }, []);

  async function loadHistory() {
    try {
      const data = await getHistory();
      setHistory(data);
    } catch {
      // Non-critical — don't show an error if history fails
    }
  }

  async function handleSearch(e) {
    e.preventDefault();
    const ticker = tickerInput.trim().toUpperCase();
    if (!ticker) return;

    setLoading(true);
    setError(null);
    setActiveMemo(null);

    try {
      const memo = await triggerResearch(ticker);
      setActiveMemo(memo);
      // Prepend to history list (summary fields only)
      setHistory(prev => [
        {
          id: memo.id,
          ticker: memo.ticker,
          date: memo.date,
          verdict: memo.verdict,
          conviction_score: memo.conviction_score,
          status: memo.status ?? 'PENDING',
          created_at: new Date().toISOString(),
        },
        ...prev,
      ]);
    } catch (err) {
      const detail = err?.response?.data?.detail ?? err.message ?? 'Unknown error';
      setError(`Research failed for ${ticker}: ${detail}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleHistoryClick(row) {
    setLoadingMemo(true);
    setActiveMemo(null);
    setError(null);
    try {
      const full = await getLatestMemo(row.ticker);
      setActiveMemo(full);
    } catch {
      // Fall back to whatever summary data we have
      setActiveMemo(row);
    } finally {
      setLoadingMemo(false);
    }
  }

  function handleStatusChange(memoId, newStatus) {
    setHistory(prev =>
      prev.map(row => (row.id === memoId ? { ...row, status: newStatus } : row))
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="mx-auto max-w-5xl px-4 py-8 space-y-8">
        {/* Page Title */}
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Research</h1>
          <p className="mt-1 text-sm text-gray-500">
            Generate an AI investment memo for any US ticker. Analysis takes 15–45 seconds.
          </p>
        </div>

        {/* Search Bar */}
        <form onSubmit={handleSearch} className="flex gap-3">
          <input
            ref={inputRef}
            type="text"
            value={tickerInput}
            onChange={e => setTickerInput(e.target.value.toUpperCase())}
            placeholder="Enter ticker (e.g. AAPL)"
            maxLength={10}
            disabled={loading}
            className="flex-1 rounded-lg border border-gray-300 px-4 py-2.5 text-sm font-mono uppercase shadow-sm placeholder:text-gray-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:bg-gray-100"
          />
          <button
            type="submit"
            disabled={loading || !tickerInput.trim()}
            className="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? 'Analyzing…' : 'Analyze'}
          </button>
        </form>

        {/* Loading Banner */}
        {loading && (
          <div className="flex items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
            <svg className="h-4 w-4 animate-spin shrink-0" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
            <span>
              Fetching SEC filings, transcripts, and news for{' '}
              <strong>{tickerInput}</strong> and generating memo…
              This usually takes 15–45 seconds.
            </span>
          </div>
        )}

        {/* Error Banner */}
        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            {error}
          </div>
        )}

        {/* Loading indicator for history-click memo fetch */}
        {loadingMemo && (
          <div className="flex items-center gap-3 rounded-lg border border-gray-200 bg-white px-4 py-3 text-sm text-gray-600">
            <svg className="h-4 w-4 animate-spin shrink-0 text-blue-500" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
            </svg>
            Loading memo…
          </div>
        )}

        {/* Active Memo */}
        {activeMemo && !loading && !loadingMemo && (
          <MemoCard memo={activeMemo} onStatusChange={handleStatusChange} />
        )}

        {/* History List */}
        {history.length > 0 && (
          <div>
            <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
              Past Memos
            </h2>
            <div className="rounded-xl border border-gray-200 bg-white divide-y divide-gray-100 shadow-sm overflow-hidden">
              {history.map(row => (
                <button
                  key={row.id ?? `${row.ticker}-${row.created_at}`}
                  onClick={() => handleHistoryClick(row)}
                  className="w-full flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-4 px-4 sm:px-5 py-3 text-left hover:bg-gray-50 transition-colors"
                >
                  <div className="flex flex-wrap items-center gap-2 sm:gap-3">
                    <span
                      className={`h-2 w-2 rounded-full shrink-0 ${VERDICT_DOT[row.verdict] ?? 'bg-gray-400'}`}
                    />
                    <span className="font-mono font-semibold text-sm text-gray-900">
                      {row.ticker}
                    </span>
                    <span className="text-xs text-gray-400">{row.date}</span>
                  </div>
                  <div className="flex w-full sm:w-auto items-center justify-between sm:justify-end gap-3">
                    <ConvictionBadge score={row.conviction_score} />
                    <span className="text-xs text-gray-400 uppercase tracking-wide w-16 text-right">
                      {row.status}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
