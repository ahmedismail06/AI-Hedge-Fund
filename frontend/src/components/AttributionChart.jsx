import { useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, ReferenceLine,
} from 'recharts';

const fmt$ = (v) => {
  if (v == null) return '—';
  const abs = Math.abs(v);
  const pfx = v < 0 ? '-' : '';
  if (abs >= 1_000) return `${pfx}$${(abs / 1_000).toFixed(1)}k`;
  return `${pfx}$${abs.toFixed(0)}`;
};
const fmtPct = (v) => v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;

function CustomTooltip({ active, payload, mode }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  const isPos = mode === '%' ? d.returnPct >= 0 : d.pnl >= 0;
  const color = isPos ? 'var(--green)' : 'var(--red)';

  return (
    <div style={{
      background: 'var(--chart-tooltip-bg)',
      border: `1px solid ${isPos ? 'rgba(0,217,138,0.3)' : 'rgba(255,51,71,0.3)'}`,
      borderRadius: 8, padding: '10px 14px',
      fontFamily: 'JetBrains Mono', fontSize: 11,
      boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
    }}>
      <div style={{ fontWeight: 700, color: 'var(--text)', marginBottom: 5, fontSize: 13 }}>
        {d.ticker}
        <span style={{
          marginLeft: 8, fontSize: 9, fontFamily: 'Syne', padding: '1px 5px',
          borderRadius: 3, fontWeight: 700,
          background: d.direction === 'LONG' ? 'var(--blue-bg)' : 'var(--red-bg)',
          color: d.direction === 'LONG' ? 'var(--blue)' : 'var(--red)',
          border: `1px solid ${d.direction === 'LONG' ? 'var(--blue-border)' : 'var(--red-border)'}`,
        }}>
          {d.direction}
        </span>
      </div>
      <div style={{ color, fontWeight: 700, fontSize: 13 }}>
        {fmt$(d.pnl)}
        <span style={{ fontSize: 11, marginLeft: 8, fontWeight: 400 }}>
          {fmtPct(d.returnPct)}
        </span>
      </div>
      {d.entry_price != null && d.current_price != null && (
        <div style={{ marginTop: 5, fontSize: 10, color: 'var(--text-3)' }}>
          Entry: ${d.entry_price.toFixed(2)} · Now: ${d.current_price.toFixed(2)}
        </div>
      )}
    </div>
  );
}

export default function AttributionChart({ positions, height = 120 }) {
  const [mode, setMode] = useState('$'); // '$' | '%'

  if (!positions || positions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2"
        style={{ height, color: 'var(--text-3)' }}>
        <span className="material-symbols-outlined" style={{ fontSize: 20, color: 'var(--border-2)' }}>bar_chart</span>
        <span style={{ fontSize: 11 }}>No attribution data</span>
      </div>
    );
  }

  const data = positions
    .map(p => {
      const isLong = p.direction === 'LONG';
      const entry  = p.entry_price  ?? 0;
      const curr   = p.current_price ?? entry;
      const shares = p.share_count  ?? 0;
      const pnl    = isLong ? (curr - entry) * shares : (entry - curr) * shares;
      const cost   = entry * shares;
      const returnPct = cost > 0 ? (pnl / cost) * 100 : null;
      return { ticker: p.ticker, pnl, returnPct, direction: p.direction, entry_price: entry, current_price: curr };
    })
    .sort((a, b) => b.pnl - a.pnl);

  const dataKey  = mode === '%' ? 'returnPct' : 'pnl';
  const maxAbs   = Math.max(...data.map(d => Math.abs(d[dataKey] ?? 0)), 1);

  return (
    <div>
      {/* Mode toggle */}
      <div className="flex justify-end mb-1">
        {['$', '%'].map(m => (
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
            domain={[-maxAbs, maxAbs]}
            tickFormatter={mode === '%' ? fmtPct : fmt$}
            tick={{ fontSize: 9, fill: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
            axisLine={false} tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="ticker"
            tick={{ fontSize: 10, fill: 'var(--text-2)', fontFamily: 'JetBrains Mono', fontWeight: 700 }}
            axisLine={false} tickLine={false}
            width={44}
          />
          <Tooltip content={<CustomTooltip mode={mode} />} />
          <ReferenceLine x={0} stroke="var(--border-2)" strokeWidth={1} />
          <Bar dataKey={dataKey} radius={[0, 3, 3, 0]} maxBarSize={14} isAnimationActive={false}>
            {data.map((d, i) => {
              const val   = d[dataKey] ?? 0;
              const isPos = val >= 0;
              const opacity = 0.55 + 0.45 * (Math.abs(val) / maxAbs);
              return (
                <Cell key={i}
                  fill={isPos ? 'var(--green)' : 'var(--red)'}
                  fillOpacity={opacity}
                />
              );
            })}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
