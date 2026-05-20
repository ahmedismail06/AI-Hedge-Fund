export default function VarGauge({ varValue, portfolioValue }) {
  const varDollar = varValue != null && portfolioValue != null
    ? Math.abs((varValue / 100) * portfolioValue)
    : null;

  const severity = varValue == null ? 'neutral'
    : varValue > -5  ? 'low'
    : varValue > -10 ? 'mid'
    : 'high';

  const COLOR = { low: 'var(--green)', mid: 'var(--amber)', high: 'var(--red)', neutral: 'var(--text-3)' };
  const color = COLOR[severity];

  const SIZE = 80;
  const R = 32;
  const CX = SIZE / 2;
  const CY = SIZE / 2;
  const CIRC = 2 * Math.PI * R;
  const HALF = CIRC * 0.65;
  const filled = varValue != null ? Math.min(Math.abs(varValue) / 20, 1) * HALF : 0;

  const startAngle = Math.PI * 0.85;
  const startX = CX + R * Math.cos(startAngle);
  const startY = CY + R * Math.sin(startAngle);
  const endAngle = startAngle + Math.PI * 1.3;
  const endX = CX + R * Math.cos(endAngle);
  const endY = CY + R * Math.sin(endAngle);

  const fmt$ = (v) => {
    if (v == null) return '—';
    if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
    if (v >= 1_000)     return `$${(v / 1_000).toFixed(1)}k`;
    return `$${v.toFixed(0)}`;
  };

  return (
    <div className="flex flex-col items-center gap-2">
      <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne', alignSelf: 'flex-start' }}>
        VaR (95%)
      </div>

      <div className="flex items-center gap-4">
        {/* SVG ring */}
        <svg width={SIZE} height={SIZE} style={{ overflow: 'visible' }}>
          {/* Track */}
          <path
            d={`M ${startX} ${startY} A ${R} ${R} 0 1 1 ${endX} ${endY}`}
            fill="none"
            stroke="var(--border)"
            strokeWidth={6}
            strokeLinecap="round"
          />
          {/* Fill */}
          {filled > 0 && (
            <path
              d={`M ${startX} ${startY} A ${R} ${R} 0 1 1 ${endX} ${endY}`}
              fill="none"
              stroke={color}
              strokeWidth={6}
              strokeLinecap="round"
              strokeDasharray={`${filled} ${HALF - filled + 1}`}
            />
          )}
          {/* Center text */}
          <text x={CX} y={CY - 4} textAnchor="middle" style={{ fontFamily: 'JetBrains Mono', fontSize: '13px', fontWeight: 800, fill: color }}>
            {varValue != null ? `${varValue.toFixed(1)}%` : '—'}
          </text>
          <text x={CX} y={CY + 10} textAnchor="middle" style={{ fontFamily: 'Syne', fontSize: '8px', fill: 'var(--text-3)' }}>
            1-day 95%
          </text>
        </svg>

        {/* Dollar amount */}
        <div>
          <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em' }}>
            Dollar VaR
          </div>
          <div style={{ fontFamily: 'JetBrains Mono', fontSize: '16px', fontWeight: 700, color, marginTop: '2px' }}>
            {fmt$(varDollar)}
          </div>
          <div style={{ fontSize: '10px', color: 'var(--text-3)', marginTop: '2px' }}>
            Max expected loss
          </div>
        </div>
      </div>
    </div>
  );
}
