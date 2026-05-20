export default function StopLadderGauge({ regime, currentDrawdown }) {
  const isRiskOff = regime === 'Risk-Off' || regime === 'Stagflation';
  const tiers = isRiskOff
    ? { tier1: -5, tier2: -10, tier3: -15 }
    : { tier1: -8, tier2: -15, tier3: -20 };

  const dd = currentDrawdown ?? 0;
  const maxScale = tiers.tier3;
  const pct = (v) => Math.min(Math.abs(v / maxScale) * 100, 100);
  const ddPct = pct(dd);

  const getTier = () => {
    if (dd <= tiers.tier3) return 3;
    if (dd <= tiers.tier2) return 2;
    if (dd <= tiers.tier1) return 1;
    return 0;
  };
  const tier = getTier();

  const TIER_COLORS = ['var(--green)', 'var(--amber)', 'var(--red)', 'var(--red)'];
  const ddColor = TIER_COLORS[tier];

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
          Stop Ladder
        </div>
        {isRiskOff && (
          <span style={{ fontSize: '9px', fontWeight: 700, fontFamily: 'Syne', padding: '1px 5px', borderRadius: '3px', background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-border)' }}>
            Risk-Off mode
          </span>
        )}
      </div>

      {/* Gauge bar */}
      <div className="relative rounded-full overflow-hidden" style={{ height: '8px', background: 'var(--border)' }}>
        <div
          style={{
            width: `${ddPct}%`,
            height: '100%',
            background: ddColor,
            transition: 'width 0.4s ease',
          }}
        />
        {/* Tier markers */}
        {[tiers.tier1, tiers.tier2, tiers.tier3].map((t, i) => (
          <div
            key={i}
            className="absolute top-0 h-full"
            style={{
              left: `${pct(t)}%`,
              width: '1.5px',
              background: 'var(--surface)',
              opacity: 0.7,
            }}
          />
        ))}
      </div>

      {/* Tier labels */}
      <div className="flex justify-between mt-2">
        {[
          { label: 'T1', pct: tiers.tier1, color: 'var(--amber)' },
          { label: 'T2', pct: tiers.tier2, color: 'var(--orange)' },
          { label: 'T3', pct: tiers.tier3, color: 'var(--red)' },
        ].map(({ label, pct: t, color }) => (
          <div key={label} style={{ textAlign: 'center' }}>
            <div style={{ fontSize: '9px', fontWeight: 700, color, fontFamily: 'Syne' }}>{label}</div>
            <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>{t}%</div>
          </div>
        ))}
      </div>

      {/* Current drawdown */}
      <div
        className="mt-3 rounded-lg px-3 py-2 flex items-center justify-between"
        style={{ background: 'var(--surface-2)', border: '1px solid var(--border)' }}
      >
        <span style={{ fontSize: '11px', color: 'var(--text-2)', fontFamily: 'Syne' }}>Portfolio Drawdown</span>
        <span style={{
          fontFamily: 'JetBrains Mono', fontSize: '13px', fontWeight: 700,
          color: tier === 0 ? 'var(--green)' : ddColor,
        }}>
          {dd !== 0 ? `${dd.toFixed(2)}%` : '0%'}
        </span>
      </div>
    </div>
  );
}
