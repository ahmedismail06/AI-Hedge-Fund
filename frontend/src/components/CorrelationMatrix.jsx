function correlationColor(v) {
  if (v == null) return 'var(--surface-3)';
  const abs = Math.abs(v);
  if (v > 0) {
    const a = Math.round(abs * 60);
    return `rgba(255,51,71,${abs * 0.7})`;
  }
  return `rgba(0,217,138,${abs * 0.7})`;
}

function fmtCorr(v) {
  if (v == null) return '—';
  return v.toFixed(2);
}

export default function CorrelationMatrix({ positions }) {
  if (!positions || positions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-6">
        <span className="material-symbols-outlined" style={{ fontSize: '24px', color: 'var(--border-2)' }}>grid_on</span>
        <span style={{ fontSize: '11px', color: 'var(--text-3)' }}>No positions</span>
      </div>
    );
  }

  const tickers = [...new Set(positions.map(p => p.ticker))].slice(0, 8);

  if (tickers.length < 2) {
    return (
      <div className="text-center py-4" style={{ fontSize: '11px', color: 'var(--text-3)' }}>
        Need ≥ 2 positions for correlation
      </div>
    );
  }

  const corrMap = {};
  tickers.forEach(a => {
    tickers.forEach(b => {
      corrMap[`${a}:${b}`] = a === b ? 1 : null;
    });
  });

  const cellSize = Math.floor(Math.min(220, 260) / tickers.length);

  return (
    <div>
      <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne', marginBottom: '8px' }}>
        Correlation Matrix
        <span style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0, marginLeft: '6px' }}>
          (returns unavailable — monitoring only)
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: `auto repeat(${tickers.length}, ${cellSize}px)`, gap: '2px', overflowX: 'auto' }}>
        {/* Header row */}
        <div />
        {tickers.map(t => (
          <div
            key={t}
            style={{ fontSize: '9px', fontWeight: 700, color: 'var(--text-2)', fontFamily: 'JetBrains Mono', textAlign: 'center', padding: '2px' }}
          >
            {t.slice(0, 4)}
          </div>
        ))}

        {/* Rows */}
        {tickers.map(a => (
          <>
            <div
              key={`row-${a}`}
              style={{ fontSize: '9px', fontWeight: 700, color: 'var(--text-2)', fontFamily: 'JetBrains Mono', display: 'flex', alignItems: 'center', paddingRight: '4px' }}
            >
              {a.slice(0, 4)}
            </div>
            {tickers.map(b => {
              const val = corrMap[`${a}:${b}`];
              return (
                <div
                  key={`${a}:${b}`}
                  style={{
                    width: `${cellSize}px`,
                    height: `${cellSize}px`,
                    borderRadius: '3px',
                    background: correlationColor(val),
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: '8px',
                    fontFamily: 'JetBrains Mono',
                    color: a === b ? 'var(--text-2)' : 'transparent',
                  }}
                  title={`${a} × ${b}: ${val != null ? fmtCorr(val) : 'N/A'}`}
                >
                  {a === b ? '1.0' : ''}
                </div>
              );
            })}
          </>
        ))}
      </div>

      <div className="flex items-center gap-2 mt-3">
        <div style={{ width: '24px', height: '6px', borderRadius: '2px', background: 'rgba(0,217,138,0.7)' }} />
        <span style={{ fontSize: '9px', color: 'var(--text-3)' }}>Negative (diversifying)</span>
        <div style={{ width: '24px', height: '6px', borderRadius: '2px', background: 'rgba(255,51,71,0.7)', marginLeft: '8px' }} />
        <span style={{ fontSize: '9px', color: 'var(--text-3)' }}>Positive (correlated)</span>
      </div>
    </div>
  );
}
