export const REGIME_CAPS = {
  'Risk-On': 150,
  'Risk-Off': 80,
  'Transitional': 120,
  'Stagflation': 100,
};

function Bar({ pct, cap, label, color, overColor }) {
  const fillPct = Math.min((pct / cap) * 100, 100);
  const isOver = pct > cap;
  const fillColor = isOver ? overColor : pct / cap > 0.95 ? '#f59e0b' : pct / cap > 0.85 ? '#fbbf24' : color;

  return (
    <div className="mb-3">
      <div className="flex justify-between text-xs text-gray-500 mb-1">
        <span>{label}</span>
        <span className={isOver ? 'text-red-600 font-semibold' : 'text-gray-600'}>
          {pct != null ? `${pct.toFixed(1)}%` : '—'} / cap {cap}%
        </span>
      </div>
      <div className="relative h-4 rounded-full bg-gray-100 overflow-visible">
        <div
          className="absolute top-0 left-0 h-full rounded-full transition-all duration-500"
          style={{ width: `${fillPct}%`, backgroundColor: fillColor }}
        />
        {/* Cap marker */}
        <div
          className="absolute top-0 h-full w-0.5 bg-gray-400 opacity-60"
          style={{ left: '100%' }}
        />
      </div>
      {isOver && (
        <p className="text-xs text-red-600 mt-1 font-medium">OVER LIMIT — new trades blocked</p>
      )}
    </div>
  );
}

export default function ExposureBar({ grossPct, netPct, capPct, regime }) {
  const resolvedCap = capPct ?? REGIME_CAPS[regime] ?? 100;
  // Net exposure shown on a ±cap scale, centered at 0
  const netAbsPct = Math.abs(netPct ?? 0);
  const netIsShort = (netPct ?? 0) < 0;

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-700">Portfolio Exposure</h3>
        {regime && (
          <span className="text-xs text-gray-400">Regime cap ({regime}): {resolvedCap}%</span>
        )}
      </div>

      <Bar pct={grossPct} cap={resolvedCap} label="Gross Exposure" color="#3b82f6" overColor="#ef4444" />

      {/* Net bar — centered */}
      <div className="mb-1">
        <div className="flex justify-between text-xs text-gray-500 mb-1">
          <span>Net Exposure {netIsShort ? '(net short)' : '(net long)'}</span>
          <span>{netPct != null ? `${netPct.toFixed(1)}%` : '—'}</span>
        </div>
        <div className="relative h-4 rounded-full bg-gray-100">
          <div className="absolute top-0 left-1/2 h-full w-0.5 bg-gray-300" />
          <div
            className="absolute top-0 h-full rounded-full bg-indigo-400 transition-all duration-500"
            style={{
              width: `${(netAbsPct / resolvedCap) * 50}%`,
              left: netIsShort ? `calc(50% - ${(netAbsPct / resolvedCap) * 50}%)` : '50%',
            }}
          />
        </div>
      </div>
    </div>
  );
}
