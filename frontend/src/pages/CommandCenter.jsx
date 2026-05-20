import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { getPositions, getPending, approveTrade, rejectTrade } from '../api/portfolio';
import { getAlerts, getCriticalAlerts } from '../api/risk';
import { getPMDecisions, getPMStatus, overrideDecision } from '../api/pm';
import PriceChart from '../components/PriceChart';
import PositionTableCompact from '../components/PositionTableCompact';
import AttributionChart from '../components/AttributionChart';
import OrchestratorBrain from '../components/OrchestratorBrain';
import DecisionCard from '../components/DecisionCard';
import TickerDrawer from '../components/TickerDrawer';
import ConfirmDialog from '../components/ConfirmDialog';

const DECISION_STATUS_STYLE = {
  SENT_TO_EXECUTION:  { color: 'var(--green)',  bg: 'var(--green-bg)' },
  BLOCKED:            { color: 'var(--red)',    bg: 'var(--red-bg)' },
  DEFERRED:           { color: 'var(--amber)',  bg: 'var(--amber-bg)' },
  PENDING_HUMAN:      { color: 'var(--blue)',   bg: 'var(--blue-bg)' },
  NO_ACTION:          { color: 'var(--text-3)', bg: 'var(--surface-2)' },
  TRIGGERED_PIPELINE: { color: 'var(--accent)', bg: 'var(--accent-muted)' },
};

function SL({ children }) {
  return (
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
      {children}
    </div>
  );
}

function Panel({ children, className = '', style = {} }) {
  return (
    <div className={`panel ${className}`} style={style}>
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

export default function CommandCenter() {
  const navigate = useNavigate();
  const { isAdmin } = useAuth();

  const [positions,   setPositions]   = useState([]);
  const [pending,     setPending]     = useState([]);
  const [criticals,   setCriticals]   = useState([]);
  const [decisions,   setDecisions]   = useState([]);
  const [pmStatus,    setPmStatus]    = useState(null);
  const [selectedTicker, setSelectedTicker] = useState(null);
  const [selectedMemo,   setSelectedMemo]   = useState(null);
  const [chartTab,    setChartTab]    = useState(null);
  const [confirm,     setConfirm]     = useState(null);
  const [memoMap,     setMemoMap]     = useState({});

  const loadData = useCallback(async () => {
    try {
      const [pos, pend, crit, dec, status] = await Promise.all([
        getPositions(),
        getPending(),
        getCriticalAlerts(),
        getPMDecisions({ limit: 10 }),
        getPMStatus(),
      ]);
      setPositions(Array.isArray(pos) ? pos : []);
      setPending(Array.isArray(pend) ? pend : []);
      setCriticals(Array.isArray(crit) ? crit : []);
      setDecisions(Array.isArray(dec) ? dec : []);
      setPmStatus(status);
    } catch {}
  }, []);

  useEffect(() => {
    loadData();
    const id = setInterval(loadData, 30_000);
    return () => clearInterval(id);
  }, [loadData]);

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
  const displayPositions = Object.values(groupedPositions);

  const handleTickerClick = (position, memo) => {
    setSelectedTicker(position);
    setSelectedMemo(memo ?? null);
    setChartTab(position.ticker);
  };

  const handleOverride = async (decisionId, type) => {
    try {
      await overrideDecision(decisionId, { override_type: type, reason: `Human override via Command Center — ${type}` });
      loadData();
    } catch {}
  };

  const handleConfirm = async () => {
    if (!confirm) return;
    const { action, id } = confirm;
    setPending(prev => prev.filter(p => p.id !== id));
    setConfirm(null);
    try {
      if (action === 'approve') await approveTrade(id);
      else await rejectTrade(id);
    } catch { loadData(); }
  };

  const tickers = displayPositions.map(p => p.ticker);

  return (
    <div className="p-4 animate-fade-in" style={{ maxWidth: '1600px', margin: '0 auto' }}>
      {confirm && (
        <ConfirmDialog
          title={confirm.action === 'approve' ? `Approve ${confirm.ticker}?` : `Reject ${confirm.ticker}?`}
          message={confirm.action === 'approve' ? 'This will send the order to the execution engine.' : 'This will reject the recommendation.'}
          confirmLabel={confirm.action === 'approve' ? 'Approve' : 'Reject'}
          destructive={confirm.action === 'reject'}
          onConfirm={handleConfirm}
          onCancel={() => setConfirm(null)}
        />
      )}

      {selectedTicker && (
        <TickerDrawer
          position={selectedTicker}
          memo={selectedMemo}
          onClose={() => { setSelectedTicker(null); setSelectedMemo(null); }}
        />
      )}

      {/* Critical alert banner */}
      {criticals.length > 0 && (
        <div
          className="rounded-xl px-5 py-3 flex items-center gap-3 cursor-pointer mb-4 transition-opacity hover:opacity-90"
          style={{ background: 'var(--red-bg)', border: '1px solid var(--red-border)' }}
          onClick={() => navigate('/risk')}
        >
          <span className="w-2 h-2 rounded-full flex-shrink-0 dot-red pulse-critical" />
          <span className="font-bold text-[11px] tracking-widest uppercase flex-shrink-0" style={{ color: 'var(--red)', fontFamily: 'Syne' }}>
            Critical Alert
          </span>
          <span className="w-px h-4 flex-shrink-0" style={{ background: 'var(--red-border)' }} />
          <span className="text-[12px] truncate" style={{ color: 'var(--text)' }}>{criticals[0]?.message}</span>
          <span className="ml-auto text-[11px] font-bold flex-shrink-0" style={{ color: 'var(--red)', fontFamily: 'Syne' }}>
            View Risk →
          </span>
        </div>
      )}

      {/* Three-column layout */}
      <div style={{ display: 'grid', gridTemplateColumns: 'clamp(200px, 22vw, 300px) minmax(0, 1fr) clamp(200px, 22vw, 320px)', gap: '12px', alignItems: 'start' }}>

        {/* ── LEFT: Position list ──────────────────────────────── */}
        <div className="space-y-3">
          <Panel>
            <PanelHeader
              label="Open Book"
              title={`${displayPositions.length} position${displayPositions.length !== 1 ? 's' : ''}`}
              action="Book →"
              onAction={() => navigate('/book')}
            />
            <div className="p-2">
              <PositionTableCompact
                positions={displayPositions}
                onTickerClick={handleTickerClick}
                memos={memoMap}
              />
            </div>
          </Panel>

          {/* Pending approvals */}
          {pending.length > 0 && (
            <Panel>
              <PanelHeader
                label="Awaiting Approval"
                title={<span style={{ color: 'var(--amber)' }}>{pending.length} pending</span>}
                action="Book →"
                onAction={() => navigate('/book')}
              />
              <div className="p-3 space-y-2">
                {pending.slice(0, 2).map(item => (
                  <div
                    key={item.id}
                    className="rounded-lg p-3"
                    style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}
                  >
                    <div className="flex items-center gap-2 mb-2">
                      <span style={{ fontFamily: 'JetBrains Mono', fontWeight: 700, fontSize: '13px', color: 'var(--text)' }}>{item.ticker}</span>
                      <span style={{ fontSize: '9px', fontWeight: 700, fontFamily: 'Syne', padding: '1px 5px', borderRadius: '3px', background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-border)' }}>
                        {item.verdict}
                      </span>
                      <span className="ml-auto text-[10px]" style={{ color: 'var(--text-3)' }}>{item.size_label}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-1">
                      <button
                        onClick={() => isAdmin && setConfirm({ action: 'approve', id: item.id, ticker: item.ticker })}
                        disabled={!isAdmin}
                        className="py-1.5 text-[10px] font-bold rounded-lg transition-opacity disabled:opacity-40"
                        style={{ background: 'var(--green-bg)', border: '1px solid var(--green-border)', color: 'var(--green)', fontFamily: 'Syne', cursor: isAdmin ? 'pointer' : 'not-allowed' }}
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => isAdmin && setConfirm({ action: 'reject', id: item.id, ticker: item.ticker })}
                        disabled={!isAdmin}
                        className="py-1.5 text-[10px] font-bold rounded-lg transition-all disabled:opacity-40"
                        style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text-2)', fontFamily: 'Syne', cursor: isAdmin ? 'pointer' : 'not-allowed' }}
                      >
                        Reject
                      </button>
                    </div>
                  </div>
                ))}
                {pending.length > 2 && (
                  <button
                    onClick={() => navigate('/book')}
                    className="w-full text-[10px] py-1.5 rounded-lg"
                    style={{ color: 'var(--text-3)', fontFamily: 'Syne', background: 'none', border: 'none', cursor: 'pointer' }}
                  >
                    +{pending.length - 2} more →
                  </button>
                )}
              </div>
            </Panel>
          )}
        </div>

        {/* ── CENTER: Charts + Attribution ─────────────────────── */}
        <div className="space-y-3">
          <Panel>
            {/* Chart tab bar: Portfolio + individual tickers */}
            <div
              className="flex items-center gap-0 overflow-x-auto no-scrollbar"
              style={{ borderBottom: '1px solid var(--border)', paddingLeft: '4px' }}
            >
              {['Portfolio', ...tickers].map(tab => (
                <button
                  key={tab}
                  onClick={() => setChartTab(tab === 'Portfolio' ? null : tab)}
                  style={{
                    padding: '10px 14px',
                    fontSize: '11px',
                    fontWeight: 700,
                    fontFamily: 'Syne',
                    background: 'transparent',
                    border: 'none',
                    borderBottom: (tab === 'Portfolio' && chartTab === null) || tab === chartTab
                      ? '2px solid var(--accent)'
                      : '2px solid transparent',
                    color: (tab === 'Portfolio' && chartTab === null) || tab === chartTab
                      ? 'var(--accent)'
                      : 'var(--text-3)',
                    cursor: 'pointer',
                    whiteSpace: 'nowrap',
                    marginBottom: '-1px',
                    transition: 'color 0.15s',
                  }}
                >
                  {tab}
                </button>
              ))}
            </div>

            <div className="p-4">
              <PriceChart
                ticker={chartTab}
                height={220}
                key={chartTab ?? 'portfolio'}
              />
            </div>
          </Panel>

          {/* P&L Attribution */}
          {displayPositions.length > 0 && (
            <Panel>
              <PanelHeader label="P&L Attribution" title="Today's contribution by position" />
              <div className="px-4 py-3">
                <AttributionChart positions={displayPositions} height={Math.min(displayPositions.length * 22 + 20, 160)} />
              </div>
            </Panel>
          )}
        </div>

        {/* ── RIGHT: AI Decisions + Orchestrator ───────────────── */}
        <div className="space-y-3">
          {/* Orchestrator brain */}
          <OrchestratorBrain status={pmStatus} onRefresh={loadData} />

          {/* PM Decision queue */}
          <Panel>
            <PanelHeader
              label="Decision Queue"
              title={`${decisions.length} recent decisions`}
              action="Full log →"
              onAction={() => navigate('/signals')}
            />
            <div className="p-3 space-y-2 term-scroll" style={{ maxHeight: '480px', overflowY: 'auto' }}>
              {decisions.length === 0 ? (
                <div className="text-center py-6" style={{ fontSize: '12px', color: 'var(--text-3)' }}>
                  No decisions yet — run a PM cycle
                </div>
              ) : (
                decisions.map(d => (
                  <DecisionCard
                    key={d.decision_id}
                    decision={d}
                    onOverride={handleOverride}
                    onDefer={(id, ticker) => {/* defer modal — link to full page */}}
                  />
                ))
              )}
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
