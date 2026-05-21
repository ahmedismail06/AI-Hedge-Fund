import { useState, useEffect, useCallback, useRef } from 'react';
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ComposedChart, Line,
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
  } catch { return String(d); }
}

// ─── Area chart tooltip ───────────────────────────────────────────────────────

function AreaTooltip({ active, payload, label, range, startValue }) {
  if (!active || !payload?.length) return null;
  const val  = payload[0]?.value;
  const spxPct = payload.find(p => p.dataKey === 'spxPct')?.value;
  const delta  = startValue != null && val != null ? val - startValue : null;
  const deltaPct = startValue && delta != null ? (delta / startValue) * 100 : null;
  const isUp = delta == null || delta >= 0;

  return (
    <div style={{
      background: 'var(--chart-tooltip-bg)',
      border: `1px solid ${isUp ? 'rgba(0,217,138,0.28)' : 'rgba(255,51,71,0.28)'}`,
      borderRadius: 8, padding: '10px 14px',
      fontFamily: 'JetBrains Mono', fontSize: 11,
      boxShadow: '0 4px 20px rgba(0,0,0,0.5)', minWidth: 148,
    }}>
      <div style={{ color: 'var(--text-3)', marginBottom: 5, fontSize: 10 }}>
        {fmtDate(label, range)}
      </div>
      <div style={{ color: 'var(--text)', fontWeight: 700, fontSize: 14 }}>{fmtPrice(val)}</div>
      {delta != null && (
        <div style={{ color: isUp ? 'var(--green)' : 'var(--red)', fontSize: 10, marginTop: 3 }}>
          {isUp ? '+' : ''}{fmtPrice(delta)} ({isUp ? '+' : ''}{deltaPct?.toFixed(2)}%)
        </div>
      )}
      {spxPct != null && (
        <div style={{
          color: 'var(--text-3)', fontSize: 10, marginTop: 5,
          borderTop: '1px solid var(--border)', paddingTop: 5,
        }}>
          SPY &nbsp;
          <span style={{ color: spxPct >= 0 ? 'var(--blue)' : 'var(--red)' }}>
            {spxPct >= 0 ? '+' : ''}{spxPct.toFixed(2)}%
          </span>
        </div>
      )}
    </div>
  );
}

// ─── Candlestick chart (custom SVG — recharts has no native OHLC) ─────────────

function CandlestickChart({ data, height, logScale, range }) {
  const containerRef = useRef(null);
  const [cw, setCw] = useState(600);
  const [tooltip, setTooltip] = useState(null);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    setCw(el.clientWidth);
    const obs = new ResizeObserver(([e]) => setCw(e.contentRect.width));
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  const mg = { top: 8, right: 8, left: 54, bottom: 22 };
  const chartW = cw - mg.left - mg.right;
  const chartH = height - mg.top - mg.bottom;

  const valid = data.filter(d => d.h != null && d.l != null && d.o != null && d.c != null);

  if (!valid.length || chartW <= 0) {
    return <div ref={containerRef} style={{ height }} />;
  }

  const highs = valid.map(d => d.h);
  const lows  = valid.map(d => d.l);
  const pad   = (Math.max(...highs) - Math.min(...lows)) * 0.05;
  const yMin  = Math.min(...lows)  - pad;
  const yMax  = Math.max(...highs) + pad;

  const toY = (v) => {
    if (logScale && yMin > 0) {
      return chartH - (Math.log(v / yMin) / Math.log(yMax / yMin)) * chartH;
    }
    return chartH - ((v - yMin) / (yMax - yMin)) * chartH;
  };

  const slotW   = chartW / valid.length;
  const candleW = Math.max(slotW - 3, 2);

  const yTicks = 5;
  const tickVals = Array.from({ length: yTicks }, (_, i) => {
    const f = i / (yTicks - 1);
    return logScale && yMin > 0
      ? yMin * Math.pow(yMax / yMin, f)
      : yMin + f * (yMax - yMin);
  });

  const labelIndices = valid.length <= 5
    ? valid.map((_, i) => i)
    : [0, Math.floor(valid.length * 0.25), Math.floor(valid.length * 0.5), Math.floor(valid.length * 0.75), valid.length - 1];

  const handleMouseMove = (e) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const mx  = e.clientX - rect.left - mg.left;
    const idx = Math.max(0, Math.min(Math.floor(mx / slotW), valid.length - 1));
    const d   = valid[idx];
    if (!d) return setTooltip(null);
    const tipX = Math.min((idx + 0.5) * slotW + mg.left, cw - 152);
    setTooltip({ d, idx, x: Math.max(0, tipX), y: 14 });
  };

  return (
    <div ref={containerRef} style={{ position: 'relative', height }}>
      <svg
        width={cw} height={height}
        onMouseMove={handleMouseMove}
        onMouseLeave={() => setTooltip(null)}
        style={{ cursor: 'crosshair' }}
      >
        <g transform={`translate(${mg.left},${mg.top})`}>
          {/* Y grid + ticks */}
          {tickVals.map((val, i) => {
            const yp = toY(val);
            const label = val >= 1000 ? `$${(val / 1000).toFixed(1)}k`
                        : val >= 100  ? `$${val.toFixed(0)}`
                        : `$${val.toFixed(2)}`;
            return (
              <g key={i}>
                <line x1={0} y1={yp} x2={chartW} y2={yp} stroke="var(--chart-grid)" strokeWidth={1} />
                <text x={-6} y={yp + 3.5} textAnchor="end"
                  style={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
                  {label}
                </text>
              </g>
            );
          })}

          {/* Candles */}
          {valid.map((d, i) => {
            const isUp  = d.c >= d.o;
            const color = isUp ? '#00D98A' : '#FF3347';
            const slotX = i * slotW;
            const cx    = slotX + slotW / 2;
            const bx    = slotX + (slotW - candleW) / 2;
            const hy = toY(d.h);
            const ly = toY(d.l);
            const oy = toY(d.o);
            const cy = toY(d.c);
            const bTop = Math.min(oy, cy);
            const bH   = Math.max(Math.abs(cy - oy), 1.5);
            const wickW = Math.max(candleW * 0.18, 1);

            return (
              <g key={i}>
                <line x1={cx} y1={hy} x2={cx} y2={ly} stroke={color} strokeWidth={wickW} />
                <rect x={bx} y={bTop} width={candleW} height={bH}
                  fill={color} fillOpacity={isUp ? 0.85 : 1} rx={1} />
              </g>
            );
          })}

          {/* Hover crosshair */}
          {tooltip && (
            <line
              x1={(tooltip.idx + 0.5) * slotW} y1={0}
              x2={(tooltip.idx + 0.5) * slotW} y2={chartH}
              stroke="var(--text-3)" strokeWidth={1} strokeDasharray="3 3"
            />
          )}

          {/* X labels */}
          {labelIndices.filter(i => i < valid.length).map(i => (
            <text key={i}
              x={(i + 0.5) * slotW} y={chartH + 16}
              textAnchor="middle"
              style={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>
              {fmtDate(valid[i]?.t ?? valid[i]?.date, range)}
            </text>
          ))}
        </g>
      </svg>

      {/* Floating tooltip */}
      {tooltip && (() => {
        const d = tooltip.d;
        const isUp = d.c >= d.o;
        const chg  = d.c - d.o;
        const chgP = d.o ? (chg / d.o) * 100 : 0;
        return (
          <div style={{
            position: 'absolute', top: tooltip.y + 20, left: tooltip.x,
            background: 'var(--chart-tooltip-bg)',
            border: `1px solid ${isUp ? 'rgba(0,217,138,0.3)' : 'rgba(255,51,71,0.3)'}`,
            borderRadius: 8, padding: '10px 14px',
            fontFamily: 'JetBrains Mono', fontSize: 11,
            pointerEvents: 'none', zIndex: 20,
            boxShadow: '0 4px 20px rgba(0,0,0,0.5)', minWidth: 148,
          }}>
            <div style={{ color: 'var(--text-3)', marginBottom: 6, fontSize: 10 }}>
              {fmtDate(d.t ?? d.date, range)}
            </div>
            {[['O', d.o, 'var(--text-2)'], ['H', d.h, 'var(--green)'], ['L', d.l, 'var(--red)'],
              ['C', d.c, isUp ? 'var(--green)' : 'var(--red)']].map(([k, v, c]) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, marginBottom: 2 }}>
                <span style={{ color: 'var(--text-3)' }}>{k}</span>
                <span style={{ color: c, fontWeight: k === 'C' ? 700 : 400 }}>
                  {v != null ? `$${v.toFixed(2)}` : '—'}
                </span>
              </div>
            ))}
            <div style={{
              borderTop: '1px solid var(--border)', paddingTop: 4, marginTop: 4,
              color: isUp ? 'var(--green)' : 'var(--red)', fontWeight: 700,
            }}>
              {isUp ? '+' : ''}{chg.toFixed(2)} ({isUp ? '+' : ''}{chgP.toFixed(2)}%)
            </div>
            {d.v != null && (
              <div style={{ color: 'var(--text-3)', fontSize: 10, marginTop: 3 }}>
                Vol: {d.v >= 1e6 ? `${(d.v / 1e6).toFixed(2)}M` : d.v >= 1e3 ? `${(d.v / 1e3).toFixed(1)}k` : d.v}
              </div>
            )}
          </div>
        );
      })()}
    </div>
  );
}

// ─── Main PriceChart ──────────────────────────────────────────────────────────

export default function PriceChart({ ticker = null, height = 220, defaultRange = '1M' }) {
  const [range, setRange]       = useState(defaultRange);
  const [mode, setMode]         = useState('area');     // 'area' | 'candle'
  const [logScale, setLogScale] = useState(false);
  const [spx, setSpx]           = useState(false);
  const [data, setData]         = useState([]);
  const [spxData, setSpxData]   = useState([]);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError(false);
    try {
      const result = ticker
        ? await getTickerBars(ticker, range)
        : await getEquityCurve(RANGE_DAYS[range] ?? 30);
      setData(Array.isArray(result) ? result : []);
    } catch { setError(true); setData([]); }
    finally { setLoading(false); }
  }, [ticker, range]);

  const loadSpx = useCallback(async () => {
    if (!spx) return;
    try {
      const result = await getTickerBars('SPY', range);
      setSpxData(Array.isArray(result) ? result : []);
    } catch { setSpxData([]); }
  }, [spx, range]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadSpx(); }, [loadSpx]);

  // Normalise field names
  const norm = data.map(d => ({
    ...d,
    date:  d.date ?? d.t ?? d.timestamp,
    value: d.value ?? d.c ?? d.close,
    o: d.o ?? d.open,
    h: d.h ?? d.high,
    l: d.l ?? d.low,
    c: d.c ?? d.close ?? d.value,
    v: d.v ?? d.volume,
  }));

  const startValue = norm.length > 0 ? norm[0].value : null;
  const endValue   = norm.length > 0 ? norm[norm.length - 1].value : null;
  const isUp       = endValue == null || startValue == null || endValue >= startValue;
  const chartColor = isUp ? 'var(--green)' : 'var(--red)';
  const gradStop   = isUp ? '#00D98A' : '#FF3347';
  const gradId     = `priceFill_${ticker ?? 'portfolio'}`;

  const changePct = startValue && endValue != null
    ? ((endValue - startValue) / startValue) * 100
    : null;

  // Build SPY % delta aligned by date
  const spxStart = spxData.length > 0 ? (spxData[0].c ?? spxData[0].value ?? spxData[0].close) : null;
  const spxMap = {};
  if (spxStart) {
    spxData.forEach(d => {
      const k = d.t ?? d.date;
      const v = d.c ?? d.close ?? d.value;
      if (k && v != null) spxMap[k] = ((v - spxStart) / spxStart) * 100;
    });
  }

  const areaData = norm.map(d => ({
    ...d,
    spxPct: spxMap[d.date] ?? null,
  }));

  // Log domain
  const yDomain = logScale ? ['auto', 'auto'] : ['auto', 'auto'];

  const isEmpty = !loading && norm.length === 0;
  const hasOhlc = norm.length > 0 && norm[0].o != null;

  return (
    <div className="flex flex-col h-full">
      {/* Controls row */}
      <div className="flex items-center gap-1 mb-2 flex-wrap">

        {/* Range tabs */}
        <div className="flex items-center gap-0.5">
          {RANGES.map(r => (
            <button key={r} onClick={() => setRange(r)} style={{
              fontFamily: 'Syne', fontSize: '10px', fontWeight: 700,
              padding: '3px 7px', borderRadius: '5px', border: 'none', cursor: 'pointer',
              background: range === r ? 'var(--accent-muted)' : 'transparent',
              color: range === r ? 'var(--accent)' : 'var(--text-3)',
              transition: 'all 0.12s',
            }}>
              {r}
            </button>
          ))}
        </div>

        {/* Divider */}
        <div style={{ width: 1, height: 14, background: 'var(--border)', margin: '0 3px' }} />

        {/* Area / Candle toggle (only for ticker with OHLC) */}
        {ticker && (
          <div className="flex items-center gap-0.5">
            {[
              { key: 'area',   icon: 'show_chart',          title: 'Area' },
              { key: 'candle', icon: 'candlestick_chart',   title: 'Candlestick' },
            ].map(({ key, icon, title }) => (
              <button key={key} onClick={() => setMode(key)} title={title} style={{
                padding: '3px 6px', borderRadius: '5px', border: 'none', cursor: 'pointer',
                background: mode === key ? 'var(--accent-muted)' : 'transparent',
                color: mode === key ? 'var(--accent)' : 'var(--text-3)',
                display: 'flex', alignItems: 'center',
              }}>
                <span className="material-symbols-outlined" style={{ fontSize: 14 }}>{icon}</span>
              </button>
            ))}
          </div>
        )}

        {/* Log scale */}
        <button onClick={() => setLogScale(l => !l)} title="Logarithmic scale" style={{
          padding: '3px 6px', borderRadius: '5px', border: 'none', cursor: 'pointer',
          background: logScale ? 'var(--accent-muted)' : 'transparent',
          color: logScale ? 'var(--accent)' : 'var(--text-3)',
          fontFamily: 'Syne', fontSize: '9px', fontWeight: 700,
        }}>
          LOG
        </button>

        {/* SPY overlay — area mode only */}
        {ticker && mode === 'area' && (
          <button onClick={() => setSpx(s => !s)} title="SPY benchmark overlay" style={{
            padding: '3px 6px', borderRadius: '5px', border: 'none', cursor: 'pointer',
            background: spx ? 'rgba(74,158,255,0.15)' : 'transparent',
            color: spx ? 'var(--blue)' : 'var(--text-3)',
            fontFamily: 'Syne', fontSize: '9px', fontWeight: 700,
          }}>
            SPY
          </button>
        )}

        {/* Loading indicator or change badge */}
        {loading
          ? <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>…</span>
          : changePct != null && (
            <span style={{
              marginLeft: 'auto',
              fontFamily: 'JetBrains Mono', fontSize: 11, fontWeight: 700,
              color: isUp ? 'var(--green)' : 'var(--red)',
            }}>
              {isUp ? '+' : ''}{changePct.toFixed(2)}%
            </span>
          )
        }
      </div>

      {/* Chart body */}
      {error || isEmpty ? (
        <div className="flex-1 flex flex-col items-center justify-center gap-2"
          style={{ height, color: 'var(--text-3)' }}>
          <span className="material-symbols-outlined" style={{ fontSize: 24, color: 'var(--border-2)' }}>
            show_chart
          </span>
          <span style={{ fontSize: 12 }}>{error ? 'Chart unavailable' : 'No data'}</span>
        </div>

      ) : mode === 'candle' && ticker ? (
        <CandlestickChart data={norm} height={height} logScale={logScale} range={range} />

      ) : (
        <div style={{ flex: 1, minHeight: height }}>
          <ResponsiveContainer width="100%" height={height}>
            <ComposedChart data={areaData} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%"   stopColor={gradStop} stopOpacity={0.28} />
                  <stop offset="100%" stopColor={gradStop} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--chart-grid)" vertical={false} />
              <XAxis
                dataKey="date"
                tickFormatter={d => fmtDate(d, range)}
                tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
                axisLine={false} tickLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                yAxisId="price"
                scale={logScale ? 'log' : 'auto'}
                domain={yDomain}
                allowDataOverflow={logScale}
                tickFormatter={v => ticker ? `$${v >= 1000 ? `${(v/1000).toFixed(1)}k` : v.toFixed(0)}` : fmtPrice(v)}
                tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
                axisLine={false} tickLine={false}
                width={52}
              />
              {spx && (
                <YAxis
                  yAxisId="spx"
                  orientation="right"
                  hide
                  domain={['auto', 'auto']}
                />
              )}
              <Tooltip content={<AreaTooltip range={range} startValue={startValue} />} />
              <Area
                yAxisId="price"
                type="monotone"
                dataKey="value"
                stroke={chartColor}
                strokeWidth={1.5}
                fill={`url(#${gradId})`}
                dot={false}
                activeDot={{ r: 4, fill: chartColor, stroke: 'var(--surface)', strokeWidth: 2 }}
                isAnimationActive={false}
              />
              {spx && spxData.length > 0 && (
                <Line
                  yAxisId="spx"
                  type="monotone"
                  dataKey="spxPct"
                  stroke="var(--blue)"
                  strokeWidth={1.2}
                  strokeDasharray="5 3"
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
