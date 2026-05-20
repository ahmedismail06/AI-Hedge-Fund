import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
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
  return (
    <div style={{
      background: 'var(--chart-tooltip-bg)',
      border: '1px solid var(--border-2)',
      borderRadius: '6px',
      padding: '8px 12px',
      fontFamily: 'JetBrains Mono',
      fontSize: '11px',
    }}>
      <div style={{ color: 'var(--text-2)', marginBottom: 2 }}>{label} Treasury</div>
      <div style={{ color: 'var(--accent)', fontWeight: 700 }}>
        {payload[0].value != null ? `${payload[0].value.toFixed(2)}%` : '—'}
      </div>
    </div>
  );
}

export default function YieldCurveChart({ indicators = {}, height = 160 }) {
  const data = MATURITIES.map(m => {
    const key = INDICATOR_MAP[m];
    const val = indicators[key];
    return {
      maturity: m,
      yield: val != null ? parseFloat(val) : null,
    };
  }).filter(d => d.yield != null);

  const isInverted = data.length >= 2 &&
    data[data.length - 1].yield < data[0].yield;

  if (data.length < 2) {
    return (
      <div className="flex flex-col items-center justify-center gap-2" style={{ height, color: 'var(--text-3)' }}>
        <span className="material-symbols-outlined" style={{ fontSize: '24px', color: 'var(--border-2)' }}>ssid_chart</span>
        <span style={{ fontSize: '11px' }}>Yield curve data unavailable</span>
      </div>
    );
  }

  const lineColor = isInverted ? 'var(--red)' : 'var(--green)';

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
          Yield Curve
        </div>
        {isInverted && (
          <span style={{ fontSize: '9px', fontWeight: 700, fontFamily: 'Syne', padding: '1px 5px', borderRadius: '3px', background: 'var(--red-bg)', color: 'var(--red)', border: '1px solid var(--red-border)' }}>
            INVERTED
          </span>
        )}
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <XAxis
            dataKey="maturity"
            tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tickFormatter={v => `${v.toFixed(1)}%`}
            tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
            axisLine={false}
            tickLine={false}
            width={36}
          />
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine y={0} stroke="var(--border-2)" strokeDasharray="3 3" />
          <Line
            type="monotone"
            dataKey="yield"
            stroke={lineColor}
            strokeWidth={2}
            dot={{ r: 3, fill: lineColor, stroke: 'var(--surface)', strokeWidth: 2 }}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
