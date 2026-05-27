import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { cancelOrder, getFills, getExecutionStatus, getOrders, runExecutionCycle } from '../api/execution';
import ConfirmDialog from '../components/ConfirmDialog';
import { BarChart, Bar, ReferenceLine, ResponsiveContainer, Tooltip as RTooltip } from 'recharts';
import { SkeletonPanel, SkeletonStat, SkeletonChart } from '../components/Skeleton';

/* ─── Helper components ──────────────────────────────────────────── */
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

function CardHeader({ label, title, action, onAction }) {
  return (
    <div className="flex items-start justify-between mb-4">
      <div>
        <SL>{label}</SL>
        {title && <div className="text-[13px] font-semibold mt-0.5" style={{ color: 'var(--text)' }}>{title}</div>}
      </div>
      {action && (
        <button onClick={onAction} className="text-[11px] font-bold transition-opacity hover:opacity-70" style={{ color: 'var(--accent)', fontFamily: 'Syne' }}>
          {action}
        </button>
      )}
    </div>
  );
}

/* ─── Status configs ─────────────────────────────────────────────── */
const STATUS_CONFIG = {
  SUBMITTED:     { color: 'var(--blue)',   bg: 'var(--blue-bg)',   border: 'var(--blue-border)',   dotColor: 'var(--blue)',  pulse: true  },
  PARTIAL:       { color: 'var(--amber)',  bg: 'var(--amber-bg)',  border: 'var(--amber-border)',  dotColor: 'var(--amber)', pulse: true  },
  PARTIAL_FILLED:{ color: 'var(--amber)',  bg: 'var(--amber-bg)',  border: 'var(--amber-border)',  dotColor: 'var(--amber)', pulse: false },
  FILLED:        { color: 'var(--green)',  bg: 'var(--green-bg)',  border: 'var(--green-border)',  dotColor: 'var(--green)', pulse: false },
  CANCELLED:     { color: 'var(--text-3)', bg: 'var(--surface-2)', border: 'var(--border)',        dotColor: 'var(--text-3)',pulse: false },
  TIMEOUT:       { color: 'var(--red)',    bg: 'var(--red-bg)',    border: 'var(--red-border)',    dotColor: 'var(--red)',  pulse: false },
  REJECTED:      { color: 'var(--red)',    bg: 'var(--red-bg)',    border: 'var(--red-border)',    dotColor: 'var(--red)',  pulse: false },
  ERROR:         { color: 'var(--red)',    bg: 'var(--red-bg)',    border: 'var(--red-border)',    dotColor: 'var(--red)',  pulse: false },
  PENDING:       { color: 'var(--text-3)', bg: 'var(--surface-2)', border: 'var(--border)',        dotColor: 'var(--text-3)',pulse: false },
};

const SIDE_CONFIG = {
  BUY:  { color: 'var(--green)', bg: 'var(--green-bg)', border: 'var(--green-border)', dotColor: 'var(--green)'  },
  SELL: { color: 'var(--red)',   bg: 'var(--red-bg)',   border: 'var(--red-border)',   dotColor: 'var(--red)'    },
};

const EXIT_TYPE_CONFIG = {
  EXIT_CLOSE: { label: 'CLOSE', color: 'var(--red)',   bg: 'var(--red-bg)',   border: 'var(--red-border)'   },
  EXIT_TRIM:  { label: 'TRIM',  color: 'var(--amber)', bg: 'var(--amber-bg)', border: 'var(--amber-border)' },
  ENTRY_ADD:  { label: 'ADD',   color: 'var(--blue)',  bg: 'var(--blue-bg)',  border: 'var(--blue-border)'  },
};

/* ─── Market status helpers ──────────────────────────────────────── */
function getMarketStatus() {
  const etStr = new Date().toLocaleString('en-US', { timeZone: 'America/New_York' });
  const et = new Date(etStr);
  const day = et.getDay();
  const mins = et.getHours() * 60 + et.getMinutes();

  if (day === 0 || day === 6)
    return { label: 'Closed', sub: 'Weekend', colorKey: 'gray', minsToNext: null };
  if (mins < 7 * 60)
    return { label: 'Closed', sub: `Pre-market opens at 07:00 ET`, colorKey: 'gray', minsToNext: 7 * 60 - mins };
  if (mins < 9 * 60 + 30)
    return { label: 'Pre-Market', sub: 'Exit orders only · Market opens at 09:30 ET', colorKey: 'amber', minsToNext: 9 * 60 + 30 - mins };
  if (mins < 15 * 60 + 55)
    return { label: 'Market Open', sub: 'Full trading active', colorKey: 'green', minsToNext: 15 * 60 + 55 - mins };
  return { label: 'After Hours', sub: 'No new orders until tomorrow 07:00 ET', colorKey: 'gray', minsToNext: null };
}

const MKT_COLORS = {
  green: { text: 'var(--green)',  bg: 'var(--green-bg)',  border: 'var(--green-border)',  dot: 'var(--green)'  },
  amber: { text: 'var(--amber)',  bg: 'var(--amber-bg)',  border: 'var(--amber-border)',  dot: 'var(--amber)'  },
  gray:  { text: 'var(--text-3)', bg: 'var(--surface-2)', border: 'var(--border)',        dot: 'var(--text-3)' },
};

/* ─── Date helpers ───────────────────────────────────────────────── */
function formatEt(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('en-US', {
    timeZone: 'America/New_York',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }) + ' ET';
}

function formatDateEt(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric' });
}

function isTodayEt(ts) {
  if (!ts) return false;
  const d = new Date(ts);
  const etNow = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }));
  const etD  = new Date(d.toLocaleString('en-US', { timeZone: 'America/New_York' }));
  return etD.toDateString() === etNow.toDateString();
}

function orderSide(o) {
  return o.order_side || (o.direction === 'LONG' ? 'BUY' : 'SELL');
}

/* ─── Component ──────────────────────────────────────────────────── */
export default function Execution() {
  const { isAdmin } = useAuth();
  const [orders, setOrders] = useState([]);
  const [fills, setFills] = useState([]);
  const [execStatus, setExecStatus] = useState(null);
  const [running, setRunning] = useState(false);
  const [confirm, setConfirm] = useState(null);
  const [marketStatus, setMarketStatus] = useState(getMarketStatus());
  const [loading, setLoading] = useState(true);
  const autoCycleRef = useRef(false);

  const load = () => {
    const p1 = getOrders({ limit: 100 }).then(r => setOrders(Array.isArray(r) ? r : (r?.data || []))).catch(() => {});
    const p2 = getFills().then(r => setFills(Array.isArray(r) ? r : (r?.data || []))).catch(() => {});
    const p3 = getExecutionStatus()
      .then(r => {
        const data = r?.data || r || {};
        const connected = data.connected ?? data.ibkr_connected ?? false;
        const env = data.env ?? (data.is_paper == null ? null : (data.is_paper ? 'paper' : 'live'));
        setExecStatus({ ...data, connected, env });
      })
      .catch(() => {});
    return Promise.all([p1, p2, p3]);
  };

  useEffect(() => {
    load().finally(() => setLoading(false));
    const pollId = setInterval(load, 10_000);
    const clockId = setInterval(() => setMarketStatus(getMarketStatus()), 30_000);
    const cycleId = setInterval(async () => {
      if (autoCycleRef.current) return;
      autoCycleRef.current = true;
      try { await runExecutionCycle(); } catch {}
      finally { autoCycleRef.current = false; load(); }
    }, 20_000);
    return () => { clearInterval(pollId); clearInterval(clockId); clearInterval(cycleId); };
  }, []);

  const todayOrders = orders.filter(o => isTodayEt(o.submitted_at || o.created_at));
  const allTimelineOrders = [...orders].sort(
    (a, b) => new Date(b.submitted_at || b.created_at) - new Date(a.submitted_at || a.created_at)
  );

  const activeCount = orders.filter(o => ['SUBMITTED', 'PARTIAL', 'PENDING'].includes(o.status)).length;
  const todayFills = fills.filter(f => isTodayEt(f.fill_time));
  const filledToday = todayFills.length;
  const cancelledCount = orders.filter(o => o.status === 'CANCELLED').length;
  const timeoutCount = orders.filter(o => o.status === 'TIMEOUT').length;

  const avgSlippage = todayFills.length
    ? (todayFills.reduce((s, f) => s + (f.slippage_bps ?? 0), 0) / todayFills.length).toFixed(1)
    : null;
  const totalCommission = todayFills.reduce((s, f) => s + (f.commission ?? 0), 0);
  const requestedQty = orders.reduce((s, o) => s + Number(o.requested_qty ?? 0), 0);
  const filledQty = orders.reduce((s, o) => s + Number(o.total_filled_qty ?? 0), 0);
  const fillRate = requestedQty > 0 ? ((filledQty / requestedQty) * 100).toFixed(0) : null;

  const orderSideMap = Object.fromEntries(orders.map(o => [o.id, orderSide(o)]));

  const todayBuys  = todayOrders.filter(o => orderSide(o) === 'BUY').length;
  const todaySells = todayOrders.filter(o => orderSide(o) === 'SELL').length;

  const handleCancel = async () => {
    if (!confirm) return;
    try { await cancelOrder(confirm.id); load(); } catch {}
    finally { setConfirm(null); }
  };

  const handleRunCycle = async () => {
    setRunning(true);
    try { await runExecutionCycle(); load(); } catch {}
    finally { setRunning(false); }
  };

  const ibkrOk = execStatus?.ibkr_connected === true;
  const isPaper = execStatus?.env === 'paper';
  const slippageChartData = todayFills.map((f, i) => ({ i: i + 1, slippage: f.slippage_bps ?? 0, ticker: f.ticker }));

  const mkt = MKT_COLORS[marketStatus.colorKey] || MKT_COLORS.gray;

  if (loading) return (
    <div className="p-4 page-wrap" style={{ maxWidth: '1600px', margin: '0 auto' }}>
      <div className="panel rounded-xl p-4 mb-3"><SkeletonStat style={{ border: 'none', padding: 0, background: 'none' }} /></div>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3 mb-3">
        {[...Array(3)].map((_, i) => <SkeletonStat key={i} />)}
      </div>
      <div className="space-y-3">
        <SkeletonPanel label="Orders" rows={6} />
        <SkeletonPanel label="Fills" rows={5} />
      </div>
    </div>
  );

  return (
    <div className="p-4 page-wrap animate-fade-in" style={{ maxWidth: '1600px', margin: '0 auto' }}>

      {/* ── Status Bar ── */}
      <div
        className="rounded-xl border p-4 flex flex-wrap items-center gap-4"
        style={{
          background: ibkrOk ? 'var(--green-bg)' : 'var(--red-bg)',
          border: `1px solid ${ibkrOk ? 'var(--green-border)' : 'var(--red-border)'}`,
        }}
      >
        <div className="flex items-center gap-2">
          <span
            className={`w-3 h-3 rounded-full${ibkrOk ? '' : ' animate-pulse'}`}
            style={{ background: ibkrOk ? 'var(--green)' : 'var(--red)' }}
          />
          <span className="font-bold text-base" style={{ color: ibkrOk ? 'var(--green)' : 'var(--red)', fontFamily: 'Syne' }}>
            IBKR Gateway: {ibkrOk ? 'CONNECTED' : 'DISCONNECTED'}
          </span>
        </div>

        {/* Paper / Live badge */}
        <span
          className="text-xs px-3 py-1 rounded-full font-bold"
          style={isPaper
            ? { background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-border)' }
            : { background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-border)' }
          }
        >
          {isPaper ? 'PAPER TRADING' : 'LIVE TRADING'}
        </span>

        {/* Market Hours Pill */}
        <div
          className="flex items-center gap-2 px-3 py-1 rounded-full text-sm font-semibold"
          style={{ background: mkt.bg, border: `1px solid ${mkt.border}` }}
        >
          <span className="w-2 h-2 rounded-full" style={{ background: mkt.dot }} />
          <span style={{ color: mkt.text }}>{marketStatus.label}</span>
        </div>

        <span className="text-xs hidden sm:block" style={{ color: 'var(--text-3)' }}>{marketStatus.sub}</span>

        {!ibkrOk && (
          <p className="text-sm ml-2" style={{ color: 'var(--red)' }}>Orders cannot be submitted until the gateway is connected.</p>
        )}

        <div className="w-full sm:w-auto sm:ml-auto flex items-center gap-3">
          <span className="text-xs" style={{ color: 'var(--text-3)' }}>Auto-refreshing every 10s</span>
          <button
            onClick={handleRunCycle}
            disabled={running || !isAdmin}
            title={!isAdmin ? 'Guest mode — read only' : undefined}
            className="px-4 py-2 text-sm font-bold rounded-lg disabled:opacity-50 disabled:cursor-not-allowed transition-opacity hover:opacity-80"
            style={{ background: 'var(--accent)', color: '#000', fontFamily: 'Syne' }}
          >
            {running ? 'Running…' : 'Run Cycle'}
          </button>
        </div>
      </div>

      {/* ── Summary Stats ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: 'Active Orders',  value: activeCount, color: activeCount > 0 ? 'var(--amber)' : 'var(--text)' },
          { label: 'Filled Today',   value: filledToday, color: 'var(--text)' },
          { label: 'Timeouts Today', value: timeoutCount, color: timeoutCount > 3 ? 'var(--red)' : 'var(--text)' },
          { label: 'Fill Rate',      value: fillRate != null ? `${fillRate}%` : '—', color: fillRate >= 90 ? 'var(--green)' : fillRate >= 50 ? 'var(--amber)' : 'var(--text)' },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded-xl p-5" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
            <SL>{label}</SL>
            <div className="text-[28px] font-bold leading-none mt-2" style={{ color, fontFamily: 'JetBrains Mono' }}>
              {value}
            </div>
          </div>
        ))}
      </div>

      {/* ── Fill Quality Stats ── */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="rounded-xl p-5" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
          <SL>Avg Slippage</SL>
          <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-3)' }}>Today's fills</div>
          <div
            className="text-[28px] font-bold leading-none mt-2"
            style={{
              fontFamily: 'JetBrains Mono',
              color: avgSlippage == null ? 'var(--text)' : Number(avgSlippage) <= 5 ? 'var(--green)' : Number(avgSlippage) <= 20 ? 'var(--amber)' : 'var(--red)',
            }}
          >
            {avgSlippage != null ? `${avgSlippage} bps` : '—'}
          </div>
        </div>
        <div className="rounded-xl p-5" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
          <SL>Total Commission</SL>
          <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-3)' }}>Today's fills</div>
          <div className="text-[28px] font-bold leading-none mt-2" style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }}>
            {totalCommission > 0 ? `$${totalCommission.toFixed(2)}` : '—'}
          </div>
        </div>
        <div className="rounded-xl p-5" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
          <SL>Today's Orders</SL>
          <div className="text-[28px] font-bold leading-none mt-2" style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }}>
            <span style={{ color: 'var(--green)' }}>{todayBuys}B</span>
            <span style={{ color: 'var(--text-3)' }}> · </span>
            <span style={{ color: 'var(--red)' }}>{todaySells}S</span>
          </div>
        </div>
      </div>

      {/* ── Order Activity Timeline ── */}
      <section className="panel overflow-hidden">
        <div className="px-5 py-4 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
          <div>
            <SL>Order Activity</SL>
            <div className="text-[13px] font-semibold mt-0.5" style={{ color: 'var(--text)' }}>Chronological buy &amp; sell events</div>
            <div className="text-[11px] mt-0.5" style={{ color: 'var(--text-3)' }}>Times in ET</div>
          </div>
          <div className="flex items-center gap-4 text-xs" style={{ color: 'var(--text-3)' }}>
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full" style={{ background: 'var(--green)' }} />
              <span style={{ fontFamily: 'Syne' }}>Buy</span>
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full" style={{ background: 'var(--red)' }} />
              <span style={{ fontFamily: 'Syne' }}>Sell</span>
            </span>
          </div>
        </div>

        {allTimelineOrders.length === 0 ? (
          <p className="px-5 py-8 text-sm text-center" style={{ color: 'var(--text-3)' }}>No order activity</p>
        ) : (
          <div>
            {allTimelineOrders.map((o, idx) => {
              const side = orderSide(o);
              const sc = SIDE_CONFIG[side] || SIDE_CONFIG.BUY;
              const cfg = STATUS_CONFIG[o.status] || STATUS_CONFIG.PENDING;
              const filledPct = Number(o.requested_qty) > 0
                ? Math.min(100, (Number(o.total_filled_qty) / Number(o.requested_qty)) * 100)
                : 0;
              const isActive = ['SUBMITTED', 'PARTIAL'].includes(o.status);
              const ts = o.submitted_at || o.created_at;
              const dateLabel = isTodayEt(ts) ? null : formatDateEt(ts);

              return (
                <div
                  key={o.id}
                  className="flex items-start gap-4 px-5 py-3 transition-colors"
                  style={{
                    background: isActive ? 'rgba(74,158,255,0.05)' : 'transparent',
                    borderBottom: idx < allTimelineOrders.length - 1 ? '1px solid var(--border)' : 'none',
                  }}
                  onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = 'var(--surface-2)'; }}
                  onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = 'transparent'; }}
                >
                  {/* Timeline spine */}
                  <div className="flex flex-col items-center pt-1 shrink-0">
                    <span
                      className={`w-3 h-3 rounded-full${isActive ? ' animate-pulse' : ''}`}
                      style={{ background: sc.dotColor }}
                    />
                    {idx < allTimelineOrders.length - 1 && (
                      <span className="w-px flex-1 mt-1 min-h-[1.5rem]" style={{ borderLeft: '1px solid var(--border)' }} />
                    )}
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      {/* Side badge */}
                      <span
                        className="text-xs font-bold px-2 py-0.5 rounded"
                        style={{ background: sc.bg, color: sc.color, border: `1px solid ${sc.border}` }}
                      >
                        {side}
                      </span>
                      {/* Ticker */}
                      <span className="font-bold text-sm" style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }}>{o.ticker}</span>
                      {/* Qty */}
                      <span className="text-sm" style={{ color: 'var(--text-2)' }}>
                        {Number(o.total_filled_qty || 0).toFixed(0)}
                        <span style={{ color: 'var(--text-3)' }}> / {Number(o.requested_qty || 0).toFixed(0)} shares</span>
                      </span>
                      {/* Order type */}
                      <span className="text-xs" style={{ color: 'var(--text-3)' }}>{o.order_type}</span>
                      {/* Exit/add type badge */}
                      {o.exit_type && EXIT_TYPE_CONFIG[o.exit_type] && (
                        <span
                          className="text-xs font-bold px-2 py-0.5 rounded"
                          style={{
                            background: EXIT_TYPE_CONFIG[o.exit_type].bg,
                            color: EXIT_TYPE_CONFIG[o.exit_type].color,
                            border: `1px solid ${EXIT_TYPE_CONFIG[o.exit_type].border}`,
                          }}
                        >
                          {EXIT_TYPE_CONFIG[o.exit_type].label}
                        </span>
                      )}
                      {/* Status badge */}
                      <div
                        className="inline-flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded-full"
                        style={{ background: cfg.bg, color: cfg.color, border: `1px solid ${cfg.border}` }}
                      >
                        <span
                          className={`w-1.5 h-1.5 rounded-full${cfg.pulse ? ' animate-pulse' : ''}`}
                          style={{ background: cfg.dotColor }}
                        />
                        {o.status}
                      </div>
                    </div>

                    {/* Fill progress bar */}
                    {Number(o.requested_qty) > 0 && (
                      <div className="mt-1.5 flex items-center gap-2">
                        <div className="flex-1 max-w-[120px] h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}>
                          <div
                            className="h-full rounded-full transition-all"
                            style={{ width: `${filledPct}%`, background: side === 'BUY' ? 'var(--green)' : 'var(--red)' }}
                          />
                        </div>
                        <span className="text-[11px]" style={{ color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>{filledPct.toFixed(0)}% filled</span>
                      </div>
                    )}

                    {/* Timestamps */}
                    <div className="flex flex-wrap gap-3 mt-1 text-[11px]" style={{ color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
                      {dateLabel && <span style={{ color: 'var(--text-2)', fontFamily: 'Syne' }}>{dateLabel}</span>}
                      {o.submitted_at && <span>Submitted {formatEt(o.submitted_at)}</span>}
                      {o.filled_at    && <span style={{ color: 'var(--green)' }}>Filled {formatEt(o.filled_at)}</span>}
                      {o.timeout_at && o.status === 'TIMEOUT' && <span style={{ color: 'var(--red)' }}>Timed out {formatEt(o.timeout_at)}</span>}
                      {o.cancelled_at && <span>Cancelled {formatEt(o.cancelled_at)}</span>}
                      {o.avg_fill_price && <span>@ ${Number(o.avg_fill_price).toFixed(2)} avg</span>}
                    </div>
                  </div>

                  {/* Cancel button */}
                  <div className="shrink-0">
                    {['SUBMITTED', 'PARTIAL', 'PENDING'].includes(o.status) ? (
                      <button
                        onClick={() => setConfirm({ id: o.id, ticker: o.ticker })}
                        disabled={!isAdmin}
                        title={!isAdmin ? 'Guest mode — read only' : undefined}
                        className="text-xs px-3 py-1.5 rounded-lg font-medium transition-opacity hover:opacity-80 disabled:opacity-40 disabled:cursor-not-allowed"
                        style={{ color: 'var(--red)', background: 'var(--red-bg)', border: '1px solid var(--red-border)' }}
                      >
                        Cancel
                      </button>
                    ) : (
                      <span className="text-xs" style={{ color: 'var(--border)' }}>—</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ── Fills Table ── */}
      <section className="panel overflow-hidden">
        <div className="px-5 py-4 flex items-center justify-between" style={{ borderBottom: '1px solid var(--border)' }}>
          <div>
            <SL>Fills</SL>
            <div className="text-[13px] font-semibold mt-0.5" style={{ color: 'var(--text)' }}>Recent Fills</div>
          </div>
          <span className="text-xs" style={{ color: 'var(--text-3)' }}>{todayFills.length} fills today</span>
        </div>
        <div className="responsive-table-wrap">
          <table className="responsive-table mobile-stack w-full text-left">
            <thead>
              <tr style={{ background: 'var(--surface-2)' }}>
                {['Ticker', 'Side', 'Fill Qty', 'Fill Price', 'Slippage', 'Commission', 'Time (ET)'].map((h, i) => (
                  <th
                    key={h}
                    className="px-5 py-3"
                    style={{
                      fontSize: '11px',
                      fontWeight: 700,
                      letterSpacing: '0.08em',
                      textTransform: 'uppercase',
                      color: 'var(--text-3)',
                      fontFamily: 'Syne',
                      textAlign: i === 6 ? 'right' : 'left',
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {fills.length === 0 && (
                <tr className="table-empty-row">
                  <td colSpan="7" className="px-5 py-8 text-sm text-center" style={{ color: 'var(--text-3)' }}>No fills yet</td>
                </tr>
              )}
              {fills.map(f => {
                const slip = f.slippage_bps != null ? Number(f.slippage_bps) : null;
                const slipColor = slip == null ? 'var(--text-3)' : slip > 20 ? 'var(--red)' : slip > 0 ? 'var(--amber)' : 'var(--green)';
                const side = orderSideMap[f.order_id] || 'BUY';
                const sc = SIDE_CONFIG[side] || SIDE_CONFIG.BUY;
                return (
                  <tr
                    key={f.id}
                    className="transition-colors"
                    style={{ borderTop: '1px solid var(--border)' }}
                    onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-2)'}
                    onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                  >
                    <td className="px-5 py-4 font-bold" style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }} data-label="Ticker">{f.ticker}</td>
                    <td className="px-5 py-4" data-label="Side">
                      <span
                        className="text-xs font-bold px-2 py-0.5 rounded"
                        style={{ background: sc.bg, color: sc.color, border: `1px solid ${sc.border}` }}
                      >
                        {side}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-sm font-medium" style={{ color: 'var(--text-2)' }} data-label="Fill Qty">{Number(f.fill_qty || 0).toFixed(0)}</td>
                    <td className="px-5 py-4 text-sm" style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }} data-label="Fill Price">${Number(f.fill_price || 0).toFixed(2)}</td>
                    <td className="px-5 py-4 text-sm font-bold" style={{ color: slipColor, fontFamily: 'JetBrains Mono' }} data-label="Slippage">
                      {slip != null ? `${slip.toFixed(1)} bps` : '—'}
                    </td>
                    <td className="px-5 py-4 text-sm" style={{ color: 'var(--text-2)' }} data-label="Commission">
                      {f.commission != null ? `$${Number(f.commission).toFixed(2)}` : '—'}
                    </td>
                    <td className="px-5 py-4 text-sm text-right" style={{ color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }} data-label="Time">{formatEt(f.fill_time)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── Slippage Chart ── */}
      {slippageChartData.length > 1 && (
        <div className="rounded-xl p-5" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
          <div className="mb-3">
            <SL>Slippage Chart</SL>
            <div className="text-[13px] font-semibold mt-0.5" style={{ color: 'var(--text)' }}>
              Slippage per Fill Today
              <span className="text-xs font-normal ml-2" style={{ color: 'var(--text-3)' }}>(bps · red line = 20 bps threshold)</span>
            </div>
          </div>
          <ResponsiveContainer width="100%" height={100}>
            <BarChart data={slippageChartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <Bar dataKey="slippage" fill="var(--accent)" radius={[3, 3, 0, 0]} />
              <ReferenceLine y={20} stroke="var(--red)" strokeDasharray="3 3" />
              <RTooltip
                formatter={(v, _, { payload }) => [`${v} bps`, payload?.ticker ?? 'Fill']}
                contentStyle={{ fontSize: 11, borderRadius: 6, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {confirm && (
        <ConfirmDialog
          title={`Cancel ${confirm.ticker} order?`}
          message="This will cancel the live IBKR order. A new order will not be submitted unless you re-approve the position."
          confirmLabel="Cancel Order"
          destructive
          onConfirm={handleCancel}
          onCancel={() => setConfirm(null)}
        />
      )}
    </div>
  );
}
