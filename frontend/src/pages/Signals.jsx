import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { getWatchlist, runScreener } from '../api/screener';
import { getHistory as getMemoHistory, triggerResearch, getLatestMemo } from '../api/research';
import { getPMDecisions, getPMConfig } from '../api/pm';
import SignalPipeline from '../components/SignalPipeline';
import MemoCardCompact from '../components/MemoCardCompact';
import MemoDetailDrawer from '../components/MemoDetailDrawer';
import { SkeletonPanel, SkeletonBlock } from '../components/Skeleton';

const SECTOR_FILTERS = ['All', 'Technology', 'Healthcare', 'Consumer', 'Industrials', 'Energy', 'Financials', 'Materials', 'Real Estate', 'Utilities'];

const SCORE_COLOR = (s) => {
  if (s >= 8) return 'var(--green)';
  if (s >= 7) return 'var(--accent)';
  if (s >= 6) return 'var(--amber)';
  return 'var(--red)';
};

function SL({ children }) {
  return (
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
      {children}
    </div>
  );
}

function Panel({ children, style = {} }) {
  return <div className="panel" style={style}>{children}</div>;
}

function PanelHeader({ label, title, action, onAction }) {
  return (
    <div className="panel-header">
      <div>
        <SL>{label}</SL>
        {title && <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text)', marginTop: '1px' }}>{title}</div>}
      </div>
      {action && (
        <button
          onClick={onAction}
          style={{ fontSize: '10px', fontWeight: 700, color: 'var(--accent)', fontFamily: 'Syne', background: 'none', border: 'none', cursor: 'pointer' }}
        >
          {action}
        </button>
      )}
    </div>
  );
}

function ScoreBar({ value, max = 10 }) {
  const pct = ((value ?? 0) / max) * 100;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
      <div style={{ flex: 1, height: '4px', borderRadius: '2px', background: 'var(--border)' }}>
        <div style={{ width: `${pct}%`, height: '100%', borderRadius: '2px', background: SCORE_COLOR(value) }} />
      </div>
      <span style={{ fontSize: '10px', fontWeight: 700, fontFamily: 'JetBrains Mono', color: SCORE_COLOR(value), minWidth: '22px', textAlign: 'right' }}>
        {value?.toFixed(1) ?? '—'}
      </span>
    </div>
  );
}

export default function Signals() {
  const { isAdmin } = useAuth();
  const [watchlist, setWatchlist]   = useState([]);
  const [memos, setMemos]           = useState([]);
  const [pmDecisions, setPmDecisions] = useState([]);
  const [sectorFilter, setSectorFilter] = useState('All');
  const [running, setRunning]       = useState(false);
  const [researchTicker, setResearchTicker] = useState('');
  const [researchStatus, setResearchStatus] = useState(null);
  const [sortKey, setSortKey]       = useState('composite_score');
  const [beneishOnly, setBeneishOnly] = useState(false);
  const [drawerMemo, setDrawerMemo] = useState(null);
  const [drawerLoading, setDrawerLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [pmConfig, setPmConfig] = useState(null);

  const loadData = useCallback(async () => {
    try {
      const [wl, mh, dec, cfg] = await Promise.all([
        getWatchlist(true),
        getMemoHistory(),
        getPMDecisions({ limit: 20 }),
        getPMConfig(),
      ]);
      setWatchlist(Array.isArray(wl) ? wl : []);
      setMemos(Array.isArray(mh) ? mh : []);
      setPmDecisions(Array.isArray(dec) ? dec : []);
      setPmConfig(cfg ?? null);
    } catch {} finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    const t = setInterval(loadData, 60_000);
    return () => clearInterval(t);
  }, [loadData]);

  const handleRunScreener = async () => {
    setRunning(true);
    try { await runScreener(); await loadData(); } catch {}
    setRunning(false);
  };

  const handleOpenMemo = async (ticker) => {
    setDrawerLoading(true);
    setDrawerMemo(null);
    try {
      const full = await getLatestMemo(ticker);
      setDrawerMemo(full);
    } catch {
      setDrawerMemo(null);
    } finally {
      setDrawerLoading(false);
    }
  };

  const handleResearch = async () => {
    const t = researchTicker.trim().toUpperCase();
    if (!t) return;
    setResearchStatus('queuing');
    try {
      await triggerResearch(t);
      setResearchStatus('queued');
      setResearchTicker('');
      setTimeout(() => { setResearchStatus(null); loadData(); }, 3000);
    } catch {
      setResearchStatus('error');
      setTimeout(() => setResearchStatus(null), 3000);
    }
  };

  // Pipeline counts — keys must match STAGES in SignalPipeline.jsx
  const universeCount  = 800;
  const screenedCount  = watchlist.length;
  // PENDING_PM_REVIEW = research done, awaiting PM decision
  const researchCount  = memos.filter(m => m.status === 'PENDING_PM_REVIEW').length;
  const memosDoneCount = memos.filter(m => ['APPROVED', 'REJECTED', 'WATCHLIST', 'DEFERRED'].includes(m.status)).length;
  const decisionsCount = pmDecisions.filter(d => d.status === 'PENDING_HUMAN').length;

  // Filter + sort screener table
  const filtered = watchlist
    .filter(w => {
      if (sectorFilter !== 'All' && w.sector !== sectorFilter) return false;
      if (beneishOnly && (w.beneish_m_score == null || w.beneish_m_score <= -1.78)) return false;
      return true;
    })
    .sort((a, b) => (b[sortKey] ?? 0) - (a[sortKey] ?? 0));

  const todayStr = new Date().toISOString().slice(0, 10);
  const screenerRanToday = watchlist.some(w => w.scored_at?.startsWith(todayStr));
  const screenerIsRunning = pmConfig?.screener_is_running ?? running;
  const screenerLastRun = pmConfig?.screener_last_run ?? null;

  const COL_HEADERS = [
    { key: 'composite_score', label: 'Score' },
    { key: 'quality_score',   label: 'Quality' },
    { key: 'value_score',     label: 'Value' },
    { key: 'momentum_score',  label: 'Momentum' },
  ];

  if (loading) return (
    <div className="p-4 page-wrap" style={{ maxWidth: '1600px', margin: '0 auto' }}>
      <div className="panel" style={{ marginBottom: '12px', padding: '16px' }}>
        <SkeletonBlock height={36} />
      </div>
      <div className="page-grid-2col-split">
        <SkeletonPanel label="Screener Watchlist" rows={10} />
        <div className="space-y-3">
          <SkeletonPanel label="Research Memos" rows={4} />
          <SkeletonPanel rows={3} />
        </div>
      </div>
    </div>
  );

  return (
    <div className="p-4 page-wrap animate-fade-in" style={{ maxWidth: '1600px', margin: '0 auto' }}>

      {/* Pipeline header */}
      <Panel style={{ marginBottom: '12px' }}>
        <div className="p-4">
          <div className="flex items-center justify-between mb-3">
            <div>
              <SL>Signal Pipeline</SL>
              <div style={{ fontSize: '12px', color: 'var(--text-2)', marginTop: '2px' }}>
                {screenerIsRunning
                  ? <span style={{ color: 'var(--amber)' }}>◎ Screener running…</span>
                  : screenerRanToday
                    ? <span style={{ color: 'var(--green)' }}>● Screener ran today</span>
                    : <span style={{ color: 'var(--text-3)' }}>○ Awaiting today's run</span>}
              </div>
            </div>
            {isAdmin && (
              <button
                onClick={handleRunScreener}
                disabled={screenerIsRunning}
                style={{
                  fontSize: '10px', fontWeight: 700, fontFamily: 'Syne',
                  padding: '5px 12px', borderRadius: '6px',
                  background: 'var(--accent)', color: '#000',
                  border: 'none', cursor: screenerIsRunning ? 'not-allowed' : 'pointer',
                  opacity: screenerIsRunning ? 0.6 : 1,
                }}
              >
                {screenerIsRunning ? 'Running…' : 'Run Screener'}
              </button>
            )}
          </div>
          <SignalPipeline
            counts={{
              universe:  universeCount,
              screened:  screenedCount,
              research:  researchCount,
              memos:     memosDoneCount,
              decisions: decisionsCount,
            }}
            screenerRunning={screenerIsRunning}
            screenerRunAt={screenerLastRun}
          />
        </div>
      </Panel>

      {/* Two-column: screener left, memos right */}
      <div className="page-grid-2col-split">

        {/* ── LEFT: Screener table ── */}
        <Panel>
          <PanelHeader
            label="Screener Results"
            title={`${filtered.length} tickers`}
          />
          <div className="p-3">
            {/* Sector filter tabs */}
            <div className="flex gap-1 overflow-x-auto no-scrollbar mb-3 pb-1">
              {SECTOR_FILTERS.map(s => (
                <button
                  key={s}
                  onClick={() => setSectorFilter(s)}
                  style={{
                    fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
                    padding: '3px 8px', borderRadius: '4px', whiteSpace: 'nowrap',
                    background: sectorFilter === s ? 'var(--accent)' : 'var(--surface-2)',
                    color: sectorFilter === s ? '#000' : 'var(--text-3)',
                    border: sectorFilter === s ? 'none' : '1px solid var(--border)',
                    cursor: 'pointer',
                  }}
                >
                  {s}
                </button>
              ))}
              <label style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '9px', color: 'var(--amber)', fontFamily: 'Syne', cursor: 'pointer', marginLeft: '4px', whiteSpace: 'nowrap' }}>
                <input type="checkbox" checked={beneishOnly} onChange={e => setBeneishOnly(e.target.checked)} />
                Beneish flag
              </label>
            </div>

            {/* Sort tabs */}
            <div className="flex gap-1 mb-3">
              {COL_HEADERS.map(({ key, label }) => (
                <button
                  key={key}
                  onClick={() => setSortKey(key)}
                  style={{
                    fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
                    padding: '2px 7px', borderRadius: '3px',
                    background: sortKey === key ? 'var(--surface-3)' : 'transparent',
                    color: sortKey === key ? 'var(--text)' : 'var(--text-3)',
                    border: '1px solid var(--border)',
                    cursor: 'pointer',
                  }}
                >
                  ↓ {label}
                </button>
              ))}
            </div>

            {/* Table */}
            <div className="term-scroll" style={{ maxHeight: '480px', overflowY: 'auto' }}>
              {filtered.length === 0 ? (
                <div className="py-8 text-center" style={{ fontSize: '12px', color: 'var(--text-3)' }}>
                  No results — run screener or adjust filters
                </div>
              ) : (
                filtered.map((w, idx) => (
                  <div
                    key={w.ticker}
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '20px 56px 1fr 1fr 1fr 1fr 80px',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '7px 4px',
                      borderBottom: idx < filtered.length - 1 ? '1px solid var(--border)' : 'none',
                    }}
                  >
                    <span style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono', textAlign: 'right' }}>{idx + 1}</span>
                    <span style={{ fontFamily: 'JetBrains Mono', fontWeight: 700, fontSize: '12px', color: 'var(--text)' }}>{w.ticker}</span>
                    <ScoreBar value={w.composite_score} />
                    <ScoreBar value={w.quality_score} />
                    <ScoreBar value={w.value_score} />
                    <ScoreBar value={w.momentum_score} />
                    {w.beneish_m_score != null && w.beneish_m_score > -1.78 ? (
                      <span style={{ fontSize: '8px', fontWeight: 700, fontFamily: 'Syne', padding: '1px 5px', borderRadius: '3px', background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-border)' }}>
                        M-SCORE
                      </span>
                    ) : (
                      <span />
                    )}
                  </div>
                ))
              )}
            </div>

            {/* Column labels below table */}
            <div style={{ display: 'grid', gridTemplateColumns: '20px 56px 1fr 1fr 1fr 1fr 80px', gap: '8px', padding: '4px 4px 0', marginTop: '4px', borderTop: '1px solid var(--border)' }}>
              <span />
              <span style={{ fontSize: '8px', color: 'var(--text-3)', fontFamily: 'Syne' }}>TICKER</span>
              <span style={{ fontSize: '8px', color: 'var(--text-3)', fontFamily: 'Syne' }}>COMPOSITE</span>
              <span style={{ fontSize: '8px', color: 'var(--text-3)', fontFamily: 'Syne' }}>QUALITY</span>
              <span style={{ fontSize: '8px', color: 'var(--text-3)', fontFamily: 'Syne' }}>VALUE</span>
              <span style={{ fontSize: '8px', color: 'var(--text-3)', fontFamily: 'Syne' }}>MOMENTUM</span>
              <span />
            </div>
          </div>
        </Panel>

        {/* ── RIGHT: Research memos ── */}
        <div className="space-y-3">
          {/* Queue input */}
          {isAdmin && (
            <Panel>
              <div className="p-3">
                <SL>Queue for Research</SL>
                <div className="flex gap-2 mt-2">
                  <input
                    type="text"
                    value={researchTicker}
                    onChange={e => setResearchTicker(e.target.value.toUpperCase())}
                    onKeyDown={e => e.key === 'Enter' && handleResearch()}
                    placeholder="Enter ticker…"
                    style={{
                      flex: 1,
                      background: 'var(--surface-2)',
                      border: '1px solid var(--border)',
                      borderRadius: '6px',
                      padding: '6px 10px',
                      fontSize: '12px',
                      color: 'var(--text)',
                      fontFamily: 'JetBrains Mono',
                      outline: 'none',
                    }}
                  />
                  <button
                    onClick={handleResearch}
                    disabled={!researchTicker.trim() || researchStatus === 'queuing'}
                    style={{
                      fontSize: '10px', fontWeight: 700, fontFamily: 'Syne',
                      padding: '6px 12px', borderRadius: '6px',
                      background: 'var(--accent)', color: '#000',
                      border: 'none', cursor: 'pointer',
                      opacity: !researchTicker.trim() ? 0.5 : 1,
                    }}
                  >
                    Queue →
                  </button>
                </div>
                {researchStatus && (
                  <div style={{ fontSize: '10px', marginTop: '4px', fontFamily: 'Syne', color: researchStatus === 'error' ? 'var(--red)' : 'var(--green)' }}>
                    {researchStatus === 'queuing' ? 'Queuing…' : researchStatus === 'queued' ? 'Queued — research will start soon' : 'Error queuing ticker'}
                  </div>
                )}
              </div>
            </Panel>
          )}

          {/* Memo cards */}
          <Panel>
            <PanelHeader
              label="Research Memos"
              title={`${memos.length} total · ${memos.filter(m => m.status === 'APPROVED').length} approved · ${memos.filter(m => m.status === 'PENDING_PM_REVIEW').length} pending review`}
            />
            <div className="p-3 space-y-2 term-scroll" style={{ maxHeight: '560px', overflowY: 'auto' }}>
              {memos.length === 0 ? (
                <div className="py-8 text-center" style={{ fontSize: '12px', color: 'var(--text-3)' }}>
                  No memos yet — queue a ticker for research
                </div>
              ) : (
                memos.map(m => (
                  <MemoCardCompact
                    key={m.memo_id ?? m.id ?? m.ticker}
                    memo={m}
                    onClick={() => handleOpenMemo(m.ticker)}
                  />
                ))
              )}
            </div>
          </Panel>
        </div>
      </div>

      {/* Memo loading overlay (while fetching full memo) */}
      {drawerLoading && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 100,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <div style={{ fontSize: '12px', fontFamily: 'Syne', color: 'var(--text-2)', fontWeight: 700 }}>
            Loading memo…
          </div>
        </div>
      )}

      {/* Full memo detail drawer */}
      {drawerMemo && !drawerLoading && (
        <MemoDetailDrawer
          memo={drawerMemo}
          onClose={() => setDrawerMemo(null)}
        />
      )}
    </div>
  );
}
