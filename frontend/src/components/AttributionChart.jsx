import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine,
} from 'recharts';

const fmt$ = (v) => {
  if (v == null) return '—';
  const abs    = Math.abs(v);
  const prefix = v < 0 ? '-' : '';
  if (abs >= 1_000) return `${prefix}$${(abs / 1_000).toFixed(1)}k`;
  return `${prefix}$${abs.toFixed(0)}`;
};

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  const isPos = d.pnl >= 0;
  return (
    <div
      style={{
        background: 'var(--chart-tooltip-bg)',
        border: '1px solid var(--border-2)',
        borderRadius: '6px',
        padding: '8px 12px',
        fontFamily: 'JetBrains Mono',
        fontSize: '11px',
      }}
    >
      <div style={{ fontWeight: 700, color: 'var(--text)', marginBottom: 2 }}>{d.ticker}</div>
      <div style={{ color: isPos ? 'var(--green)' : 'var(--red)', fontWeight: 700 }}>
        {isPos ? '+' : ''}{fmt$(d.pnl)}
      </div>
    </div>
  );
}

export default function AttributionChart({ positions, height = 120 }) {
  if (!positions || positions.length === 0) {
    return (
      <div
        className="flex flex-col items-center justify-center gap-2"
        style={{ height, color: 'var(--text-3)' }}
      >
        <span className="material-symbols-outlined" style={{ fontSize: '20px', color: 'var(--border-2)' }}>bar_chart</span>
        <span style={{ fontSize: '11px' }}>No attribution data</span>
      </div>
    );
  }

  const data = positions
    .map(p => {
      const isLong  = p.direction === 'LONG';
      const entry   = p.entry_price ?? 0;
      const current = p.current_price ?? entry;
      const shares  = p.share_count ?? 0;
      const pnl     = isLong ? (current - entry) * shares : (entry - current) * shares;
      return { ticker: p.ticker, pnl };
    })
    .sort((a, b) => b.pnl - a.pnl);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 8, left: 0, bottom: 0 }}>
        <XAxis
          type="number"
          tickFormatter={fmt$}
          tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          type="category"
          dataKey="ticker"
          tick={{ fontSize: 10, fill: 'var(--text-2)', fontFamily: 'JetBrains Mono', fontWeight: 700 }}
          axisLine={false}
          tickLine={false}
          width={44}
        />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine x={0} stroke="var(--border-2)" strokeWidth={1} />
        <Bar dataKey="pnl" radius={[0, 3, 3, 0]} maxBarSize={14} isAnimationActive={false}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.pnl >= 0 ? 'var(--green)' : 'var(--red)'} fillOpacity={0.75} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
