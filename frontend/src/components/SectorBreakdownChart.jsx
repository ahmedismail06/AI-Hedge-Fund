import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts';

const SECTOR_COLORS = [
  'var(--blue)', 'var(--green)', 'var(--amber)', 'var(--orange)',
  'var(--accent)', 'var(--red)', '#9B59B6', '#1ABC9C',
];

function CustomTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
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
      <div style={{ color: 'var(--text)', fontWeight: 700 }}>{payload[0].payload.sector}</div>
      <div style={{ color: 'var(--accent)' }}>{payload[0].value.toFixed(1)}%</div>
    </div>
  );
}

export default function SectorBreakdownChart({ positions, height = 180 }) {
  if (!positions || positions.length === 0) {
    return (
      <div className="flex items-center justify-center" style={{ height, color: 'var(--text-3)', fontSize: '12px' }}>
        No sector data
      </div>
    );
  }

  const totalValue = positions.reduce((sum, p) => {
    const cost = (p.entry_price ?? 0) * (p.share_count ?? 0);
    return sum + cost;
  }, 0);

  const bySector = {};
  positions.forEach(p => {
    const sector = p.sector ?? p.gics_sector ?? 'Other';
    const cost   = (p.entry_price ?? 0) * (p.share_count ?? 0);
    bySector[sector] = (bySector[sector] ?? 0) + cost;
  });

  const data = Object.entries(bySector)
    .map(([sector, value]) => ({
      sector,
      pct: totalValue > 0 ? (value / totalValue) * 100 : 0,
    }))
    .sort((a, b) => b.pct - a.pct);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 8, left: 0, bottom: 0 }}>
        <XAxis
          type="number"
          domain={[0, 'dataMax']}
          tickFormatter={v => `${v.toFixed(0)}%`}
          tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          type="category"
          dataKey="sector"
          tick={{ fontSize: 9, fill: 'var(--text-2)', fontFamily: 'Syne', fontWeight: 600 }}
          axisLine={false}
          tickLine={false}
          width={72}
        />
        <Tooltip content={<CustomTooltip />} />
        <Bar dataKey="pct" radius={[0, 4, 4, 0]} maxBarSize={12} isAnimationActive={false}>
          {data.map((_, i) => (
            <Cell key={i} fill={SECTOR_COLORS[i % SECTOR_COLORS.length]} fillOpacity={0.7} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
