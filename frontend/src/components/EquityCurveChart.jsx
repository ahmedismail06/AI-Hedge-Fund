import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
} from 'recharts';

const fmt = (v) =>
  v >= 1_000_000 ? `$${(v / 1_000_000).toFixed(2)}M`
  : v >= 1_000   ? `$${(v / 1_000).toFixed(1)}k`
  : `$${v}`;

const fmtDate = (d) => {
  try {
    return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(new Date(d));
  } catch { return d; }
};

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div
      className="px-3 py-2 rounded-md text-[11px] font-data"
      style={{
        background: 'var(--chart-tooltip-bg)',
        border:     '1px solid var(--border-2)',
        color:      'var(--text)',
        fontFamily: 'JetBrains Mono',
        boxShadow:  'var(--card-shadow)',
      }}
    >
      <div style={{ color: 'var(--text-2)', marginBottom: 2 }}>{fmtDate(label)}</div>
      <div style={{ color: 'var(--accent)' }}>{fmt(payload[0].value)}</div>
    </div>
  );
}

export default function EquityCurveChart({ data = [], height = 200 }) {
  if (!data.length) {
    return (
      <div
        className="flex flex-col items-center justify-center gap-2 font-data text-[12px]"
        style={{ height, color: 'var(--text-3)' }}
      >
        <span
          className="material-symbols-outlined"
          style={{ fontSize: '24px', color: 'var(--border-2)' }}
        >
          show_chart
        </span>
        No equity data yet
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="curveGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%"  stopColor="var(--accent)" stopOpacity={0.22} />
            <stop offset="95%" stopColor="var(--accent)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid
          strokeDasharray="3 3"
          stroke="var(--chart-grid)"
          vertical={false}
        />
        <XAxis
          dataKey="date"
          tickFormatter={fmtDate}
          tick={{ fontSize: 10, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tickFormatter={fmt}
          tick={{ fontSize: 10, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
          axisLine={false}
          tickLine={false}
          width={60}
        />
        <Tooltip content={<CustomTooltip />} />
        <Area
          type="monotone"
          dataKey="value"
          stroke="var(--accent)"
          strokeWidth={1.5}
          fill="url(#curveGrad)"
          dot={false}
          activeDot={{ r: 4, fill: 'var(--accent)', stroke: 'var(--surface)', strokeWidth: 2 }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
