import { useEffect, useRef, useState } from 'react';
import { cancelOrder, getFills, getExecutionStatus, getOrders, runExecutionCycle } from '../api/execution';
import ConfirmDialog from '../components/ConfirmDialog';
import StatCard from '../components/StatCard';
import { BarChart, Bar, ReferenceLine, ResponsiveContainer, Tooltip as RTooltip } from 'recharts';

const STATUS_CONFIG = {
  SUBMITTED:     { classes: 'bg-blue-100 text-blue-700',   dot: 'bg-blue-500 animate-pulse' },
  PARTIAL:       { classes: 'bg-yellow-100 text-yellow-700', dot: 'bg-yellow-500 animate-pulse' },
  PARTIAL_FILLED:{ classes: 'bg-yellow-100 text-yellow-700', dot: 'bg-yellow-500' },
  FILLED:        { classes: 'bg-green-100 text-green-700',  dot: 'bg-green-500' },
  CANCELLED:     { classes: 'bg-gray-100 text-gray-500',   dot: 'bg-gray-400' },
  TIMEOUT:       { classes: 'bg-red-100 text-red-700',     dot: 'bg-red-500' },
  REJECTED:      { classes: 'bg-red-100 text-red-700',     dot: 'bg-red-600' },
  ERROR:         { classes: 'bg-red-100 text-red-700',     dot: 'bg-red-500' },
  PENDING:       { classes: 'bg-gray-100 text-gray-500',   dot: 'bg-gray-300' },
};

function getMarketStatus() {
  const etStr = new Date().toLocaleString('en-US', { timeZone: 'America/New_York' });
  const et = new Date(etStr);
  const day = et.getDay();
  const mins = et.getHours() * 60 + et.getMinutes();

  if (day === 0 || day === 6)
    return { label: 'Closed', sub: 'Weekend', color: 'gray', minsToNext: null };
  if (mins < 7 * 60)
    return { label: 'Closed', sub: `Pre-market opens at 07:00 ET`, color: 'gray', minsToNext: 7 * 60 - mins };
  if (mins < 9 * 60 + 30)
    return { label: 'Pre-Market', sub: 'Exit orders only · Market opens at 09:30 ET', color: 'yellow', minsToNext: 9 * 60 + 30 - mins };
  if (mins < 15 * 60 + 55)
    return { label: 'Market Open', sub: 'Full trading active', color: 'green', minsToNext: 15 * 60 + 55 - mins };
  return { label: 'After Hours', sub: 'No new orders until tomorrow 07:00 ET', color: 'gray', minsToNext: null };
}

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

const SIDE_STYLE = {
  BUY:  { badge: 'bg-emerald-100 text-emerald-700 border border-emerald-200', dot: 'bg-emerald-500', line: 'border-emerald-300' },
  SELL: { badge: 'bg-rose-100 text-rose-700 border border-rose-200',         dot: 'bg-rose-500',    line: 'border-rose-300' },
};

function orderSide(o) {
  return o.order_side || (o.direction === 'LONG' ? 'BUY' : 'SELL');
}

export default function Execution() {
  const [orders, setOrders] = useState([]);
  const [fills, setFills] = useState([]);
  const [execStatus, setExecStatus] = useState(null);
  const [running, setRunning] = useState(false);
  const [confirm, setConfirm] = useState(null);
  const [marketStatus, setMarketStatus] = useState(getMarketStatus());
  const autoCycleRef = useRef(false);

  const load = () => {
    getOrders({ limit: 100 }).then(r => setOrders(Array.isArray(r) ? r : (r?.data || []))).catch(() => {});
    getFills().then(r => setFills(Array.isArray(r) ? r : (r?.data || []))).catch(() => {});
    getExecutionStatus()
      .then(r => {
        const data = r?.data || r || {};
        const connected = data.connected ?? data.ibkr_connected ?? false;
        const env = data.env ?? (data.is_paper == null ? null : (data.is_paper ? 'paper' : 'live'));
        setExecStatus({ ...data, connected, env });
      })
      .catch(() => {});
  };

  useEffect(() => {
    load();
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

  const mktColor = { green: 'text-emerald-600', yellow: 'text-amber-500', gray: 'text-gray-400' };
  const mktBg    = { green: 'bg-emerald-50 border-emerald-200', yellow: 'bg-amber-50 border-amber-200', gray: 'bg-gray-50 border-gray-200' };
  const mktDot   = { green: 'bg-emerald-500 animate-pulse', yellow: 'bg-amber-400 animate-pulse', gray: 'bg-gray-400' };

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-7xl mx-auto">

      {/* ── Status Bar ── */}
      <div className={`rounded-xl border p-4 flex flex-wrap items-center gap-4 ${ibkrOk ? 'bg-green-50 border-green-200' : 'bg-red-50 border-red-200'}`}>
        <div className="flex items-center gap-2">
          <span className={`w-3 h-3 rounded-full ${ibkrOk ? 'bg-green-500' : 'bg-red-500 animate-pulse'}`} />
          <span className={`font-bold text-base ${ibkrOk ? 'text-green-700' : 'text-red-700'}`}>
            IBKR Gateway: {ibkrOk ? 'CONNECTED' : 'DISCONNECTED'}
          </span>
        </div>
        <span className={`text-sm px-3 py-1 rounded-full font-bold ${isPaper ? 'bg-yellow-100 text-yellow-700 border border-yellow-300' : 'bg-green-100 text-green-700 border border-green-300'}`}>
          {isPaper ? 'PAPER TRADING' : 'LIVE TRADING'}
        </span>
        {/* Market Hours Pill */}
        <div className={`flex items-center gap-2 px-3 py-1 rounded-full border text-sm font-semibold ${mktBg[marketStatus.color]}`}>
          <span className={`w-2 h-2 rounded-full ${mktDot[marketStatus.color]}`} />
          <span className={mktColor[marketStatus.color]}>{marketStatus.label}</span>
        </div>
        <span className="text-xs text-gray-500 hidden sm:block">{marketStatus.sub}</span>
        {!ibkrOk && (
          <p className="text-sm text-red-600 ml-2">Orders cannot be submitted until the gateway is connected.</p>
        )}
        <div className="w-full sm:w-auto sm:ml-auto flex items-center gap-3">
          <span className="text-xs text-gray-500">Auto-refreshing every 10s</span>
          <button
            onClick={handleRunCycle}
            disabled={running}
            className="px-4 py-2 text-sm font-semibold bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {running ? 'Running…' : 'Run Cycle'}
          </button>
        </div>
      </div>

      {/* ── Summary Stats ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Active Orders"  value={activeCount}  tooltip="Orders currently submitted or partially filled" status={activeCount > 0 ? 'warn' : 'ok'} />
        <StatCard label="Filled Today"   value={filledToday}  tooltip="Number of orders fully filled today" status="neutral" />
        <StatCard label="Timeouts Today" value={timeoutCount} tooltip="Orders cancelled after timeout with zero fills" status={timeoutCount > 3 ? 'warn' : 'neutral'} />
        <StatCard label="Fill Rate"      value={fillRate != null ? `${fillRate}%` : '—'} tooltip="Ratio of filled quantity to requested quantity" status={fillRate >= 90 ? 'ok' : fillRate >= 50 ? 'warn' : 'neutral'} />
      </div>

      {/* ── Fill Quality Stats ── */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <StatCard
          label="Avg Slippage" plainLabel="Today's fills"
          value={avgSlippage != null ? `${avgSlippage} bps` : '—'}
          tooltip="How much worse your fill prices were vs. order prices. Above 20 bps warrants attention."
          status={avgSlippage == null ? 'neutral' : avgSlippage <= 5 ? 'ok' : avgSlippage <= 20 ? 'warn' : 'critical'}
        />
        <StatCard
          label="Total Commission" plainLabel="Today's fills"
          value={totalCommission > 0 ? `$${totalCommission.toFixed(2)}` : '—'}
          tooltip="Total brokerage commissions paid today"
          status="neutral"
        />
        <StatCard
          label="Today's Orders"
          value={`${todayBuys}B · ${todaySells}S`}
          tooltip={`${todayBuys} buy orders and ${todaySells} sell orders submitted today`}
          status="neutral"
        />
      </div>

      {/* ── Order Activity Timeline ── */}
      <section className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">Order Activity</h3>
            <p className="text-xs text-gray-400 mt-0.5">Chronological buy &amp; sell events · times in ET</p>
          </div>
          <div className="flex items-center gap-4 text-xs text-gray-500">
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-emerald-500" />Buy</span>
            <span className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full bg-rose-500" />Sell</span>
          </div>
        </div>

        {allTimelineOrders.length === 0 ? (
          <p className="px-5 py-8 text-sm text-gray-400 text-center">No order activity</p>
        ) : (
          <div className="divide-y divide-gray-50">
            {allTimelineOrders.map((o, idx) => {
              const side = orderSide(o);
              const st = SIDE_STYLE[side] || SIDE_STYLE.BUY;
              const cfg = STATUS_CONFIG[o.status] || STATUS_CONFIG.PENDING;
              const filledPct = Number(o.requested_qty) > 0
                ? Math.min(100, (Number(o.total_filled_qty) / Number(o.requested_qty)) * 100)
                : 0;
              const isActive = ['SUBMITTED', 'PARTIAL'].includes(o.status);
              const ts = o.submitted_at || o.created_at;
              const dateLabel = isTodayEt(ts) ? null : formatDateEt(ts);

              return (
                <div key={o.id} className={`flex items-start gap-4 px-5 py-3 ${isActive ? 'bg-blue-50/40' : 'hover:bg-gray-50/60'} transition-colors`}>
                  {/* Timeline spine */}
                  <div className="flex flex-col items-center pt-1 shrink-0">
                    <span className={`w-3 h-3 rounded-full ${st.dot} ${isActive ? 'animate-pulse' : ''}`} />
                    {idx < allTimelineOrders.length - 1 && (
                      <span className="w-px flex-1 mt-1 min-h-[1.5rem] border-l border-gray-200" />
                    )}
                  </div>

                  {/* Content */}
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      {/* Side badge */}
                      <span className={`text-xs font-bold px-2 py-0.5 rounded ${st.badge}`}>{side}</span>
                      {/* Ticker */}
                      <span className="font-mono font-bold text-gray-900 text-sm">{o.ticker}</span>
                      {/* Qty */}
                      <span className="text-sm text-gray-600">
                        {Number(o.total_filled_qty || 0).toFixed(0)}
                        <span className="text-gray-400"> / {Number(o.requested_qty || 0).toFixed(0)} shares</span>
                      </span>
                      {/* Order type */}
                      <span className="text-xs text-gray-400">{o.order_type}</span>
                      {/* Status */}
                      <div className={`inline-flex items-center gap-1 text-xs font-bold px-2 py-0.5 rounded-full ${cfg.classes}`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
                        {o.status}
                      </div>
                    </div>

                    {/* Fill progress bar */}
                    {Number(o.requested_qty) > 0 && (
                      <div className="mt-1.5 flex items-center gap-2">
                        <div className="flex-1 max-w-[120px] h-1.5 bg-gray-100 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all ${side === 'BUY' ? 'bg-emerald-400' : 'bg-rose-400'}`}
                            style={{ width: `${filledPct}%` }}
                          />
                        </div>
                        <span className="text-[11px] text-gray-400">{filledPct.toFixed(0)}% filled</span>
                      </div>
                    )}

                    {/* Timestamps */}
                    <div className="flex flex-wrap gap-3 mt-1 text-[11px] text-gray-400 font-mono">
                      {dateLabel && <span className="text-gray-500 font-sans font-medium">{dateLabel}</span>}
                      {o.submitted_at && <span>Submitted {formatEt(o.submitted_at)}</span>}
                      {o.filled_at    && <span className="text-green-600">Filled {formatEt(o.filled_at)}</span>}
                      {o.timeout_at && o.status === 'TIMEOUT' && <span className="text-red-500">Timed out {formatEt(o.timeout_at)}</span>}
                      {o.cancelled_at && <span>Cancelled {formatEt(o.cancelled_at)}</span>}
                      {o.avg_fill_price && <span>@ ${Number(o.avg_fill_price).toFixed(2)} avg</span>}
                    </div>
                  </div>

                  {/* Cancel button */}
                  <div className="shrink-0">
                    {['SUBMITTED', 'PARTIAL', 'PENDING'].includes(o.status) ? (
                      <button
                        onClick={() => setConfirm({ id: o.id, ticker: o.ticker })}
                        className="text-xs text-red-600 border border-red-200 bg-red-50 hover:bg-red-100 px-3 py-1.5 rounded-lg font-medium transition-colors"
                      >
                        Cancel
                      </button>
                    ) : (
                      <span className="text-xs text-gray-300">—</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ── Fills Table ── */}
      <section className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-900">Recent Fills</h3>
          <span className="text-xs text-gray-400">{todayFills.length} fills today</span>
        </div>
        <div className="responsive-table-wrap">
          <table className="responsive-table mobile-stack w-full text-left">
            <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
              <tr>
                <th className="px-5 py-3">Ticker</th>
                <th className="px-5 py-3">Side</th>
                <th className="px-5 py-3">Fill Qty</th>
                <th className="px-5 py-3">Fill Price</th>
                <th className="px-5 py-3">Slippage</th>
                <th className="px-5 py-3">Commission</th>
                <th className="px-5 py-3 text-right">Time (ET)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {fills.length === 0 && (
                <tr className="table-empty-row"><td colSpan="7" className="px-5 py-8 text-sm text-gray-400 text-center">No fills yet</td></tr>
              )}
              {fills.map(f => {
                const slip = f.slippage_bps != null ? Number(f.slippage_bps) : null;
                const slipHigh = slip != null && slip > 20;
                const side = orderSideMap[f.order_id] || 'BUY';
                const st = SIDE_STYLE[side] || SIDE_STYLE.BUY;
                return (
                  <tr key={f.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-5 py-4 font-mono font-bold text-gray-900" data-label="Ticker">{f.ticker}</td>
                    <td className="px-5 py-4" data-label="Side">
                      <span className={`text-xs font-bold px-2 py-0.5 rounded ${st.badge}`}>{side}</span>
                    </td>
                    <td className="px-5 py-4 text-sm font-medium" data-label="Fill Qty">{Number(f.fill_qty || 0).toFixed(0)}</td>
                    <td className="px-5 py-4 text-sm font-mono" data-label="Fill Price">${Number(f.fill_price || 0).toFixed(2)}</td>
                    <td className={`px-5 py-4 text-sm font-bold ${slipHigh ? 'text-red-600' : slip != null && slip > 0 ? 'text-yellow-600' : 'text-green-600'}`} data-label="Slippage">
                      {slip != null ? `${slip.toFixed(1)} bps` : '—'}
                    </td>
                    <td className="px-5 py-4 text-sm text-gray-600" data-label="Commission">
                      {f.commission != null ? `$${Number(f.commission).toFixed(2)}` : '—'}
                    </td>
                    <td className="px-5 py-4 text-sm text-gray-400 text-right font-mono" data-label="Time">{formatEt(f.fill_time)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* ── Slippage Chart ── */}
      {slippageChartData.length > 1 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-semibold text-gray-900 mb-3">
            Slippage per Fill Today
            <span className="text-xs font-normal text-gray-400 ml-2">(bps · red line = 20 bps threshold)</span>
          </h3>
          <ResponsiveContainer width="100%" height={100}>
            <BarChart data={slippageChartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <Bar dataKey="slippage" fill="#6366f1" radius={[3, 3, 0, 0]} />
              <ReferenceLine y={20} stroke="#ef4444" strokeDasharray="3 3" />
              <RTooltip
                formatter={(v, _, { payload }) => [`${v} bps`, payload?.ticker ?? 'Fill']}
                contentStyle={{ fontSize: 11, borderRadius: 6 }}
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
