import { useEffect, useRef, useState } from 'react';
import { cancelOrder, getFills, getExecutionStatus, getOrders, runExecutionCycle } from '../api/execution';
import ConfirmDialog from '../components/ConfirmDialog';
import StatCard from '../components/StatCard';
import { BarChart, Bar, ReferenceLine, ResponsiveContainer, Tooltip as RTooltip } from 'recharts';

const STATUS_CONFIG = {
  SUBMITTED: { classes: 'bg-blue-100 text-blue-700', dot: 'bg-blue-500 animate-pulse' },
  PARTIAL:   { classes: 'bg-yellow-100 text-yellow-700', dot: 'bg-yellow-500 animate-pulse' },
  FILLED:    { classes: 'bg-green-100 text-green-700', dot: 'bg-green-500' },
  CANCELLED: { classes: 'bg-gray-100 text-gray-500', dot: 'bg-gray-400' },
  TIMEOUT:   { classes: 'bg-red-100 text-red-700', dot: 'bg-red-500' },
  ERROR:     { classes: 'bg-red-100 text-red-700', dot: 'bg-red-500' },
  PENDING:   { classes: 'bg-gray-100 text-gray-500', dot: 'bg-gray-300' },
};

function formatTs(ts) {
  if (!ts) return '—';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export default function Execution() {
  const [orders, setOrders] = useState([]);
  const [fills, setFills] = useState([]);
  const [execStatus, setExecStatus] = useState(null);
  const [running, setRunning] = useState(false);
  const [confirm, setConfirm] = useState(null);
  const autoCycleRef = useRef(false);

  const load = () => {
    getOrders().then(r => setOrders(Array.isArray(r) ? r : (r?.data || []))).catch(() => {});
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
    const cycleId = setInterval(async () => {
      if (autoCycleRef.current) return;
      autoCycleRef.current = true;
      try { await runExecutionCycle(); } catch {}
      finally { autoCycleRef.current = false; load(); }
    }, 20_000);
    return () => { clearInterval(pollId); clearInterval(cycleId); };
  }, []);

  const today = new Date().toDateString();
  const activeCount = orders.filter(o => ['SUBMITTED', 'PARTIAL', 'PENDING'].includes(o.status)).length;
  const fillsToday = fills.filter(f => f.fill_time && new Date(f.fill_time).toDateString() === today);
  const filledToday = fillsToday.length;
  const cancelledCount = orders.filter(o => o.status === 'CANCELLED').length;

  // Fill quality stats
  const avgSlippage = fillsToday.length
    ? (fillsToday.reduce((s, f) => s + (f.slippage_bps ?? 0), 0) / fillsToday.length).toFixed(1)
    : null;
  const totalCommission = fillsToday.reduce((s, f) => s + (f.commission ?? 0), 0);
  const requestedQty = orders.reduce((s, o) => s + (o.requested_qty ?? 0), 0);
  const filledQty = orders.reduce((s, o) => s + (o.total_filled_qty ?? 0), 0);
  const fillRate = requestedQty > 0 ? ((filledQty / requestedQty) * 100).toFixed(0) : null;

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

  const slippageChartData = fillsToday.map((f, i) => ({ i: i + 1, slippage: f.slippage_bps ?? 0, ticker: f.ticker }));

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-7xl mx-auto">

      {/* IBKR Status Bar */}
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

      {/* Summary Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard label="Active Orders" value={activeCount} tooltip="Orders currently submitted or partially filled" status={activeCount > 0 ? 'warn' : 'ok'} />
        <StatCard label="Filled Today" value={filledToday} tooltip="Number of orders fully filled today" status="neutral" />
        <StatCard label="Cancelled" value={cancelledCount} status="neutral" />
        <StatCard label="Fill Rate" value={fillRate != null ? `${fillRate}%` : '—'} tooltip="Ratio of filled quantity to requested quantity" status={fillRate >= 90 ? 'ok' : fillRate >= 50 ? 'warn' : 'neutral'} />
      </div>

      {/* Fill Quality Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <StatCard
          label="Avg Slippage"
          plainLabel="Today's fills"
          value={avgSlippage != null ? `${avgSlippage} bps` : '—'}
          tooltip="How much worse your fill prices were vs. order prices. Lower is better. Anything above 20 bps warrants attention."
          status={avgSlippage == null ? 'neutral' : avgSlippage <= 5 ? 'ok' : avgSlippage <= 20 ? 'warn' : 'critical'}
        />
        <StatCard
          label="Total Commission"
          plainLabel="Today's fills"
          value={totalCommission > 0 ? `$${totalCommission.toFixed(2)}` : '—'}
          tooltip="Total brokerage commissions paid today"
          status="neutral"
        />
        <StatCard
          label="Filled Qty"
          plainLabel="Across all orders"
          value={`${filledQty.toFixed(0)} / ${requestedQty.toFixed(0)} shares`}
          tooltip="Total shares filled vs. total shares requested"
          status="neutral"
        />
      </div>

      {/* Orders Table */}
      <section className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-900">Live Orders</h3>
          <span className="text-xs text-gray-400">Real-time · updates every 10s</span>
        </div>
        <div className="responsive-table-wrap">
          <table className="responsive-table mobile-stack w-full text-left">
            <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
              <tr>
                <th className="px-5 py-3">Ticker</th>
                <th className="px-5 py-3">Direction</th>
                <th className="px-5 py-3">Order Type</th>
                <th className="px-5 py-3">Requested</th>
                <th className="px-5 py-3">Filled</th>
                <th className="px-5 py-3">Status</th>
                <th className="px-5 py-3">Submitted</th>
                <th className="px-5 py-3 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {orders.length === 0 && (
                <tr className="table-empty-row"><td colSpan="8" className="px-5 py-8 text-sm text-gray-400 text-center">No orders yet</td></tr>
              )}
              {orders.map(o => {
                const cfg = STATUS_CONFIG[o.status] || STATUS_CONFIG.PENDING;
                return (
                  <tr key={o.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-5 py-4" data-label="Ticker">
                      <span className="font-mono font-bold text-gray-900">{o.ticker}</span>
                    </td>
                    <td className="px-5 py-4" data-label="Direction">
                      <span className={`text-xs px-2 py-0.5 rounded font-medium ${o.direction === 'LONG' ? 'bg-blue-100 text-blue-700' : 'bg-red-100 text-red-700'}`}>
                        {o.direction === 'LONG' ? 'BUY' : 'SELL'}
                      </span>
                    </td>
                    <td className="px-5 py-4 text-sm text-gray-700" data-label="Order Type">{o.order_type ?? '—'}</td>
                    <td className="px-5 py-4 text-sm font-medium text-gray-900" data-label="Requested">{Number(o.requested_qty || 0).toFixed(0)}</td>
                    <td className="px-5 py-4 text-sm text-gray-500" data-label="Filled">{Number(o.total_filled_qty || 0).toFixed(0)}</td>
                    <td className="px-5 py-4" data-label="Status">
                      <div className={`inline-flex items-center gap-1.5 text-xs font-bold px-2 py-1 rounded-full ${cfg.classes}`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${cfg.dot}`} />
                        {o.status}
                      </div>
                    </td>
                    <td className="px-5 py-4 text-sm text-gray-400 font-mono" data-label="Submitted">{formatTs(o.submitted_at)}</td>
                    <td className="px-5 py-4 text-right" data-label="Action">
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
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Fills Table */}
      <section className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-900">Recent Fills</h3>
          <span className="text-xs text-gray-400">{fillsToday.length} fills today</span>
        </div>
        <div className="responsive-table-wrap">
          <table className="responsive-table mobile-stack w-full text-left">
            <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
              <tr>
                <th className="px-5 py-3">Ticker</th>
                <th className="px-5 py-3">Fill Qty</th>
                <th className="px-5 py-3">Fill Price</th>
                <th className="px-5 py-3">Slippage</th>
                <th className="px-5 py-3">Commission</th>
                <th className="px-5 py-3 text-right">Time</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {fills.length === 0 && (
                <tr className="table-empty-row"><td colSpan="6" className="px-5 py-8 text-sm text-gray-400 text-center">No fills yet</td></tr>
              )}
              {fills.map(f => {
                const slip = f.slippage_bps != null ? Number(f.slippage_bps) : null;
                const slipHigh = slip != null && slip > 20;
                return (
                  <tr key={f.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-5 py-4 font-mono font-bold text-gray-900" data-label="Ticker">{f.ticker}</td>
                    <td className="px-5 py-4 text-sm font-medium" data-label="Fill Qty">{Number(f.fill_qty || 0).toFixed(0)}</td>
                    <td className="px-5 py-4 text-sm font-mono" data-label="Fill Price">${Number(f.fill_price || 0).toFixed(2)}</td>
                    <td className={`px-5 py-4 text-sm font-bold ${slipHigh ? 'text-red-600' : slip != null && slip > 0 ? 'text-yellow-600' : 'text-green-600'}`} data-label="Slippage">
                      {slip != null ? `${slip.toFixed(1)} bps` : '—'}
                    </td>
                    <td className="px-5 py-4 text-sm text-gray-600" data-label="Commission">
                      {f.commission != null ? `$${Number(f.commission).toFixed(2)}` : '—'}
                    </td>
                    <td className="px-5 py-4 text-sm text-gray-400 text-right font-mono" data-label="Time">{formatTs(f.fill_time)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Slippage Trend Chart */}
      {slippageChartData.length > 1 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h3 className="text-sm font-semibold text-gray-900 mb-3">
            Slippage per Fill Today
            <span className="text-xs font-normal text-gray-400 ml-2">(bps — lower is better, red line = 20 bps threshold)</span>
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
