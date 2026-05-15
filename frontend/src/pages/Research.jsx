// Research Page
// Ticker search → triggers memo generation → displays MemoCard
// Below: scrollable history list of past memos

import { useState, useEffect, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import MemoCard from '../components/MemoCard';
import ConvictionBadge from '../components/ConvictionBadge';
import { triggerResearch, getHistory, getLatestMemo } from '../api/research';

function SL({ children }) {
  return (
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
      {children}
    </div>
  );
}

const VERDICT_DOT_COLOR = {
  LONG:  'var(--green)',
  SHORT: 'var(--red)',
  AVOID: 'var(--text-3)',
};

export default function Research() {
  const { isAdmin } = useAuth();
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
    <div className="p-6 space-y-6 max-w-5xl mx-auto">
      {/* Page Title */}
      <div>
        <h1 style={{ color: 'var(--text)', fontFamily: 'Syne', fontWeight: 700 }} className="text-2xl">
          Research
        </h1>
        <p className="mt-1 text-sm" style={{ color: 'var(--text-3)' }}>
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
          placeholder={isAdmin ? 'Enter ticker (e.g. AAPL)' : 'Guest mode — read only'}
          maxLength={10}
          disabled={loading || !isAdmin}
          style={{
            background: 'var(--input-bg)',
            border: '1px solid var(--border)',
            color: 'var(--text)',
            fontFamily: 'JetBrains Mono',
            opacity: loading || !isAdmin ? 0.5 : 1,
            cursor: !isAdmin ? 'not-allowed' : 'text',
          }}
          className="flex-1 rounded-lg px-4 py-2.5 text-sm uppercase"
          onFocus={e => { if (isAdmin) { e.target.style.outline = 'none'; e.target.style.boxShadow = '0 0 0 2px var(--accent-ring)'; } }}
          onBlur={e => { e.target.style.boxShadow = 'none'; }}
        />
        <button
          type="submit"
          disabled={loading || !tickerInput.trim() || !isAdmin}
          title={!isAdmin ? 'Guest mode — read only' : undefined}
          style={{
            background: 'var(--accent)',
            color: '#000',
            fontFamily: 'Syne',
            opacity: (loading || !tickerInput.trim() || !isAdmin) ? 0.5 : 1,
          }}
          className="rounded-lg px-5 py-2.5 text-sm font-semibold transition-colors disabled:cursor-not-allowed"
        >
          {loading ? 'Analyzing…' : 'Analyze'}
        </button>
      </form>

      {/* Loading Banner */}
      {loading && (
        <div
          className="flex items-center gap-3 rounded-lg px-4 py-3 text-sm"
          style={{
            background: 'var(--blue-bg)',
            border: '1px solid var(--blue-border)',
            color: 'var(--blue)',
          }}
        >
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
        <div
          className="rounded-lg px-4 py-3 text-sm"
          style={{
            background: 'var(--red-bg)',
            border: '1px solid var(--red-border)',
            color: 'var(--red)',
          }}
        >
          {error}
        </div>
      )}

      {/* Loading indicator for history-click memo fetch */}
      {loadingMemo && (
        <div
          className="flex items-center gap-3 rounded-lg px-4 py-3 text-sm"
          style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            color: 'var(--text-2)',
          }}
        >
          <svg className="h-4 w-4 animate-spin shrink-0" viewBox="0 0 24 24" fill="none">
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
        <div className="space-y-3">
          <SL>Past Memos</SL>
          <div
            style={{
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: '12px',
              overflow: 'hidden',
            }}
          >
            {history.map((row, i) => (
              <button
                key={row.id ?? `${row.ticker}-${row.created_at}`}
                onClick={() => handleHistoryClick(row)}
                style={{
                  width: '100%',
                  background: 'transparent',
                  borderBottom: i < history.length - 1 ? '1px solid var(--border)' : 'none',
                  cursor: 'pointer',
                }}
                className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-4 px-4 sm:px-5 py-3 text-left transition-colors"
                onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-2)'; }}
                onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
              >
                <div className="flex flex-wrap items-center gap-2 sm:gap-3">
                  <span
                    className="h-2 w-2 rounded-full shrink-0"
                    style={{ background: VERDICT_DOT_COLOR[row.verdict] ?? 'var(--text-3)' }}
                  />
                  <span style={{ fontFamily: 'JetBrains Mono', color: 'var(--text)', fontWeight: 700 }} className="text-sm">
                    {row.ticker}
                  </span>
                  <span className="text-xs" style={{ color: 'var(--text-3)' }}>{row.date}</span>
                </div>
                <div className="flex w-full sm:w-auto items-center justify-between sm:justify-end gap-3">
                  <ConvictionBadge score={row.conviction_score} />
                  <span className="text-xs uppercase tracking-wide w-16 text-right" style={{ color: 'var(--text-3)' }}>
                    {row.status}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
