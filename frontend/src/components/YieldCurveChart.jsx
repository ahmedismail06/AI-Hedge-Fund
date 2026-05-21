import {
  ComposedChart, Area, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';

const MATURITIES = ['3m', '6m', '1y', '2y', '5y', '10y', '30y'];

const INDICATOR_MAP = {
  '3m':  'treasury_3m',
  '6m':  'treasury_6m',
  '1y':  'treasury_1y',
  '2y':  'treasury_2y',
  '5y':  'treasury_5y',
  '10y': 'treasury_10y',
  '30y': 'treasury_30y',
};

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const val    = payload[0]?.value;
  const isHigh = val != null && val > 4;
  const color  = isHigh ? 'var(--red)' : val != null && val > 2 ? 'var(--amber)' : 'var(--green)';
  return (
    <div style={{
      background: 'var(--chart-tooltip-bg)',
      border: '1px solid var(--border-2)',
      borderRadius: 8, padding: '10px 14px',
      fontFamily: 'JetBrains Mono', fontSize: 11,
      boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
    }}>
      <div style={{ color: 'var(--text-2)', marginBottom: 3, fontFamily: 'Syne', fontSize: 10 }}>
        {label} Treasury
      </div>
      <div style={{ color, fontWeight: 700, fontSize: 14 }}>
        {val != null ? `${val.toFixed(3)}%` : '—'}
      </div>
    </div>
  );
}

export default function YieldCurveChart({ indicators = {}, height = 160 }) {
  const raw = MATURITIES.map(m => {
    const key = INDICATOR_MAP[m];
    const v   = indicators[key];
    return { maturity: m, yield: v != null ? parseFloat(v) : null };
  }).filter(d => d.yield != null);

  if (raw.length < 2) {
    return (
      <div className="flex flex-col items-center justify-center gap-2"
        style={{ height, color: 'var(--text-3)' }}>
        <span className="material-symbols-outlined" style={{ fontSize: 24, color: 'var(--border-2)' }}>ssid_chart</span>
        <span style={{ fontSize: 11 }}>Yield curve data unavailable</span>
      </div>
    );
  }

  const isInverted = raw[raw.length - 1].yield < raw[0].yield;

  // 10Y-2Y spread
  const y10 = raw.find(d => d.maturity === '10y')?.yield;
  const y2  = raw.find(d => d.maturity === '2y')?.yield;
  const spread = y10 != null && y2 != null ? y10 - y2 : null;
  const spreadBps = spread != null ? Math.round(spread * 100) : null;

  const lineColor = isInverted ? 'var(--red)' : 'var(--green)';
  const gradStop  = isInverted ? '#FF3347' : '#00D98A';
  const gradId    = 'yieldCurveGrad';

  const yMin = Math.min(...raw.map(d => d.yield)) - 0.1;
  const yMax = Math.max(...raw.map(d => d.yield)) + 0.1;

  return (
    <div>
      {/* Header */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
          Yield Curve
        </div>

        {isInverted && (
          <span style={{
            fontSize: 9, fontWeight: 700, fontFamily: 'Syne',
            padding: '1px 6px', borderRadius: 3,
            background: 'var(--red-bg)', color: 'var(--red)', border: '1px solid var(--red-border)',
          }}>
            INVERTED
          </span>
        )}

        {spreadBps != null && (
          <span style={{
            marginLeft: 'auto', fontFamily: 'JetBrains Mono', fontSize: 10, fontWeight: 700,
            color: spreadBps >= 0 ? 'var(--green)' : 'var(--red)',
          }}>
            10Y−2Y: {spreadBps >= 0 ? '+' : ''}{spreadBps}bps
          </span>
        )}
      </div>

      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={raw} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor={gradStop} stopOpacity={0.25} />
              <stop offset="100%" stopColor={gradStop} stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="maturity"
            tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
            axisLine={false} tickLine={false}
          />
          <YAxis
            domain={[yMin, yMax]}
            tickFormatter={v => `${v.toFixed(1)}%`}
            tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
            axisLine={false} tickLine={false}
            width={36}
          />
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine y={0} stroke="var(--border-2)" strokeDasharray="3 3" />

          {/* Filled area */}
          <Area
            type="monotone"
            dataKey="yield"
            stroke={lineColor}
            strokeWidth={2.5}
            fill={`url(#${gradId})`}
            dot={{ r: 4, fill: lineColor, stroke: 'var(--surface)', strokeWidth: 2 }}
            activeDot={{ r: 5, fill: lineColor, stroke: 'var(--surface)', strokeWidth: 2 }}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>

      {/* Maturity value row */}
      <div className="flex justify-between mt-2">
        {raw.map(d => (
          <div key={d.maturity} className="text-center">
            <div style={{ fontSize: 8, color: 'var(--text-3)', fontFamily: 'Syne', marginBottom: 1 }}>{d.maturity}</div>
            <div style={{ fontSize: 9, fontWeight: 700, fontFamily: 'JetBrains Mono', color: lineColor }}>
              {d.yield.toFixed(2)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
