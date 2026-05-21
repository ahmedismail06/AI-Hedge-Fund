import { useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell,
} from 'recharts';

const SECTOR_COLORS = [
  'var(--blue)', 'var(--green)', 'var(--amber)', 'var(--orange)',
  'var(--accent)', 'var(--red)', '#9B59B6', '#1ABC9C',
];

function fmt$(v) {
  if (v == null) return '—';
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000)     return `$${(v / 1_000).toFixed(1)}k`;
  return `$${v.toFixed(0)}`;
}

function CustomTooltip({ active, payload, mode }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div style={{
      background: 'var(--chart-tooltip-bg)',
      border: '1px solid var(--border-2)',
      borderRadius: 8, padding: '10px 14px',
      fontFamily: 'JetBrains Mono', fontSize: 11,
      boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
    }}>
      <div style={{ color: 'var(--text)', fontWeight: 700, marginBottom: 5, fontFamily: 'Syne' }}>
        {d.sector}
      </div>
      <div style={{ color: 'var(--accent)', fontWeight: 700 }}>
        {mode === '$' ? fmt$(d.value) : `${d.pct.toFixed(1)}%`}
      </div>
      {mode === '%' && (
        <div style={{ color: 'var(--text-3)', fontSize: 10, marginTop: 3 }}>
          {fmt$(d.value)}
        </div>
      )}
      <div style={{ color: 'var(--text-3)', fontSize: 10, marginTop: 3 }}>
        {d.count} position{d.count !== 1 ? 's' : ''}
        {d.longs > 0 && d.shorts > 0 && ` · ${d.longs}L / ${d.shorts}S`}
      </div>
    </div>
  );
}

export default function SectorBreakdownChart({ positions, height = 180 }) {
  const [mode, setMode] = useState('%'); // '%' | '$'

  if (!positions || positions.length === 0) {
    return (
      <div className="flex items-center justify-center"
        style={{ height, color: 'var(--text-3)', fontSize: 12 }}>
        No sector data
      </div>
    );
  }

  const totalValue = positions.reduce((s, p) => s + (p.entry_price ?? 0) * (p.share_count ?? 0), 0);

  const bySector = {};
  positions.forEach(p => {
    const sec  = p.sector ?? p.gics_sector ?? 'Other';
    const cost = (p.entry_price ?? 0) * (p.share_count ?? 0);
    if (!bySector[sec]) bySector[sec] = { value: 0, count: 0, longs: 0, shorts: 0 };
    bySector[sec].value += cost;
    bySector[sec].count += 1;
    if (p.direction === 'LONG')  bySector[sec].longs  += 1;
    if (p.direction === 'SHORT') bySector[sec].shorts += 1;
  });

  const data = Object.entries(bySector)
    .map(([sector, s]) => ({
      sector,
      value: s.value,
      pct:   totalValue > 0 ? (s.value / totalValue) * 100 : 0,
      count: s.count,
      longs: s.longs,
      shorts: s.shorts,
    }))
    .sort((a, b) => b.value - a.value);

  const dataKey = mode === '$' ? 'value' : 'pct';
  const tickFmt = mode === '$' ? fmt$ : v => `${v.toFixed(0)}%`;

  return (
    <div>
      {/* Mode toggle */}
      <div className="flex justify-end mb-1">
        {['%', '$'].map(m => (
          <button key={m} onClick={() => setMode(m)} style={{
            fontFamily: 'Syne', fontSize: 9, fontWeight: 700,
            padding: '2px 7px', borderRadius: 4, border: 'none', cursor: 'pointer',
            background: mode === m ? 'var(--accent-muted)' : 'transparent',
            color: mode === m ? 'var(--accent)' : 'var(--text-3)',
          }}>
            {m}
          </button>
        ))}
      </div>

      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 8, left: 0, bottom: 0 }}>
          <XAxis
            type="number"
            domain={[0, 'dataMax']}
            tickFormatter={tickFmt}
            tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
            axisLine={false} tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="sector"
            tick={{ fontSize: 9, fill: 'var(--text-2)', fontFamily: 'Syne', fontWeight: 600 }}
            axisLine={false} tickLine={false}
            width={72}
          />
          <Tooltip content={<CustomTooltip mode={mode} />} />
          <Bar dataKey={dataKey} radius={[0, 4, 4, 0]} maxBarSize={12} isAnimationActive={false}>
            {data.map((_, i) => (
              <Cell key={i} fill={SECTOR_COLORS[i % SECTOR_COLORS.length]} fillOpacity={0.75} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
