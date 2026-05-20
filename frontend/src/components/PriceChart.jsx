import { useState, useEffect, useCallback } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts';
import { getEquityCurve } from '../api/portfolio';
import { getTickerBars } from '../api/market';

const RANGES = ['1D', '1W', '1M', '3M', '1Y'];

const RANGE_DAYS = { '1D': 1, '1W': 7, '1M': 30, '3M': 90, '1Y': 365 };

function fmtPrice(v) {
  if (v == null) return '—';
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000)     return `$${(v / 1_000).toFixed(1)}k`;
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtDate(d, range) {
  try {
    const dt = new Date(d);
    if (range === '1D') return dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  } catch { return d; }
}

function CustomTooltip({ active, payload, label, range, startValue }) {
  if (!active || !payload?.length) return null;
  const val = payload[0].value;
  const delta = startValue != null ? val - startValue : null;
  const deltaPct = startValue ? ((delta / startValue) * 100) : null;
  const isUp = delta == null || delta >= 0;

  return (
    <div
      className="px-3 py-2 rounded-md"
      style={{
        background: 'var(--chart-tooltip-bg)',
        border: '1px solid var(--border-2)',
        fontFamily: 'JetBrains Mono',
        fontSize: '11px',
        boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
      }}
    >
      <div style={{ color: 'var(--text-3)', marginBottom: 3 }}>{fmtDate(label, range)}</div>
      <div style={{ color: 'var(--text)', fontWeight: 700, fontSize: '13px' }}>{fmtPrice(val)}</div>
      {delta != null && (
        <div style={{ color: isUp ? 'var(--green)' : 'var(--red)', fontSize: '10px', marginTop: 2 }}>
          {isUp ? '+' : ''}{fmtPrice(delta)} ({isUp ? '+' : ''}{deltaPct?.toFixed(2)}%)
        </div>
      )}
    </div>
  );
}

export default function PriceChart({ ticker = null, height = 220, defaultRange = '1M' }) {
  const [range, setRange] = useState(defaultRange);
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      let result;
      if (ticker) {
        result = await getTickerBars(ticker, range);
      } else {
        result = await getEquityCurve(RANGE_DAYS[range] ?? 30);
      }
      setData(Array.isArray(result) ? result : []);
    } catch {
      setError(true);
      setData([]);
    } finally {
      setLoading(false);
    }
  }, [ticker, range]);

  useEffect(() => { load(); }, [load]);

  const startValue = data.length > 0 ? data[0].value ?? data[0].close : null;
  const endValue   = data.length > 0 ? data[data.length - 1].value ?? data[data.length - 1].close : null;
  const isUp       = endValue == null || startValue == null || endValue >= startValue;

  const chartColor   = isUp ? 'var(--green)' : 'var(--red)';
  const gradientId   = `priceFill_${ticker ?? 'portfolio'}`;
  const gradientStop = isUp ? '#00D98A' : '#FF3347';

  const normalised = data.map(d => ({
    ...d,
    value: d.value ?? d.close ?? d.c,
    date: d.date ?? d.t ?? d.timestamp,
  }));

  return (
    <div className="flex flex-col h-full">
      {/* Range tabs */}
      <div className="flex items-center gap-1 mb-3">
        {RANGES.map(r => (
          <button
            key={r}
            onClick={() => setRange(r)}
            style={{
              fontFamily: 'Syne',
              fontSize: '10px',
              fontWeight: 700,
              padding: '3px 8px',
              borderRadius: '5px',
              border: 'none',
              cursor: 'pointer',
              transition: 'all 0.15s',
              background: range === r ? 'var(--accent-muted)' : 'transparent',
              color: range === r ? 'var(--accent)' : 'var(--text-3)',
            }}
          >
            {r}
          </button>
        ))}
        {loading && (
          <span className="ml-auto text-[10px]" style={{ color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
            loading…
          </span>
        )}
      </div>

      {/* Chart */}
      {error || (!loading && normalised.length === 0) ? (
        <div
          className="flex-1 flex flex-col items-center justify-center gap-2"
          style={{ height, color: 'var(--text-3)' }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '24px', color: 'var(--border-2)' }}>
            show_chart
          </span>
          <span style={{ fontSize: '12px' }}>
            {error ? 'Chart unavailable' : 'No data'}
          </span>
        </div>
      ) : (
        <div style={{ flex: 1, minHeight: height }}>
          <ResponsiveContainer width="100%" height={height}>
            <AreaChart data={normalised} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%"   stopColor={gradientStop} stopOpacity={0.28} />
                  <stop offset="100%" stopColor={gradientStop} stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis
                dataKey="date"
                tickFormatter={d => fmtDate(d, range)}
                tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
              />
              <YAxis hide domain={['auto', 'auto']} />
              <Tooltip content={<CustomTooltip range={range} startValue={startValue} />} />
              <Area
                type="monotone"
                dataKey="value"
                stroke={chartColor}
                strokeWidth={1.5}
                fill={`url(#${gradientId})`}
                dot={false}
                activeDot={{ r: 4, fill: chartColor, stroke: 'var(--surface)', strokeWidth: 2 }}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
