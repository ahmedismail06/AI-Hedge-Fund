import { useState, useEffect, useCallback } from 'react';
import { getBriefing, getRegime, getMacroHistory, getIndicators, runMacroAgent } from '../api/macro';
import { LineChart, Line, Tooltip as RTooltip, ResponsiveContainer } from 'recharts';

const REGIME_STYLES = {
  'Risk-On':     { bg: 'bg-green-600',  text: 'text-green-600',  light: 'bg-green-50 border-green-200' },
  'Risk-Off':    { bg: 'bg-red-600',    text: 'text-red-600',    light: 'bg-red-50 border-red-200' },
  'Transitional':{ bg: 'bg-yellow-500', text: 'text-yellow-600', light: 'bg-yellow-50 border-yellow-200' },
  'Stagflation': { bg: 'bg-orange-500', text: 'text-orange-600', light: 'bg-orange-50 border-orange-200' },
};

const REGIME_GUIDANCE = {
  'Risk-On':     { gross: '150%', stops: 'Normal (−8% / −15% / −20%)', sizing: 'Full sizing allowed — Large/Medium/Small positions', note: 'Conditions favour adding exposure.' },
  'Risk-Off':    { gross: '80%',  stops: 'Tighter (−5% / −10% / −15%)', sizing: 'Reduce exposure — prefer Small/Micro positions', note: 'Defensive posture. Preserve capital.' },
  'Transitional':{ gross: '120%', stops: 'Normal (−8% / −15% / −20%)', sizing: 'Moderate sizing — Medium/Small preferred', note: 'Uncertain conditions — remain flexible.' },
  'Stagflation': { gross: '100%', stops: 'Tighter (−5% / −10% / −15%)', sizing: 'Cautious — Small/Micro only', note: 'Inflation + slowing growth. Hard environment.' },
};

const INDICATOR_LABELS = {
  gdp_growth:          { label: 'GDP Growth', unit: '%', desc: 'Year-over-year change in US gross domestic product' },
  cpi_yoy:             { label: 'CPI Inflation', unit: '%', desc: 'Year-over-year consumer price inflation' },
  ppi_yoy:             { label: 'PPI (Producer Prices)', unit: '%', desc: 'Year-over-year change in producer prices — a leading indicator for CPI' },
  pce_yoy:             { label: 'PCE Inflation', unit: '%', desc: 'Fed\'s preferred inflation measure (Personal Consumption Expenditures)' },
  ism_pmi:             { label: 'ISM Manufacturing PMI', unit: '', desc: 'Above 50 = manufacturing expanding; below 50 = contracting' },
  jobless_claims:      { label: 'Jobless Claims', unit: 'K/wk', desc: 'Weekly initial unemployment claims — rising = labour market weakening' },
  nonfarm_payrolls:    { label: 'Nonfarm Payrolls', unit: 'K/mo', desc: 'Monthly jobs added to the US economy' },
  breakeven_5y:        { label: '5-Year Inflation Expectation', unit: '%', desc: 'Market-implied inflation over the next 5 years' },
  treasury_10y:        { label: '10-Year Treasury Yield', unit: '%', desc: 'Benchmark long-term interest rate — rising = tighter financial conditions' },
  treasury_2y:         { label: '2-Year Treasury Yield', unit: '%', desc: 'Short-term rate sensitive to Fed policy expectations' },
  yield_curve:         { label: 'Yield Curve (10Y−2Y)', unit: 'bps', desc: 'Negative = inverted curve, historically a recession signal' },
  hy_spread:           { label: 'High-Yield Credit Spread', unit: 'bps', desc: 'Extra yield investors demand for risky bonds — rising = risk-off sentiment' },
  vix:                 { label: 'VIX (Market Fear Index)', unit: '', desc: 'S&P 500 implied volatility. Above 20 = elevated fear' },
  dxy:                 { label: 'US Dollar Index (DXY)', unit: '', desc: 'Strength of the US dollar vs a basket of currencies' },
  spx_200dma_pct:      { label: 'S&P 500 vs 200-Day Average', unit: '%', desc: 'How far the S&P 500 is above/below its 200-day moving average' },
};

function SubScoreBar({ label, value, desc }) {
  const pct = Math.round(((value ?? 0) + 1) / 2 * 100); // map -1..+1 → 0..100%
  const isPos = (value ?? 0) >= 0;
  const color = isPos ? '#10b981' : '#ef4444';
  return (
    <div className="min-w-0">
      <div className="flex justify-between items-baseline mb-1">
        <span className="text-xs font-medium text-white/80">{label}</span>
        <span className={`text-sm font-bold ${isPos ? 'text-green-300' : 'text-red-300'}`}>
          {value != null ? (value >= 0 ? '+' : '') + value.toFixed(2) : '—'}
        </span>
      </div>
      <div className="relative h-2 rounded-full bg-white/20">
        <div className="absolute top-0 left-1/2 w-0.5 h-full bg-white/40" />
        <div
          className="absolute top-0 h-full rounded-full transition-all"
          style={{
            backgroundColor: color,
            width: `${Math.abs((value ?? 0)) * 50}%`,
            left: isPos ? '50%' : `calc(50% - ${Math.abs(value ?? 0) * 50}%)`,
          }}
        />
      </div>
      {desc && <p className="text-xs text-white/60 mt-1">{desc}</p>}
    </div>
  );
}

const SUB_SCORE_META = {
  growth_score:    { label: 'Growth Outlook',           descs: { pos: 'Strong growth signals', neg: 'Slowing growth' } },
  inflation_score: { label: 'Inflation Pressure',       descs: { pos: 'High / rising inflation', neg: 'Benign inflation' } },
  fed_score:       { label: 'Federal Reserve Stance',   descs: { pos: 'Hawkish (tightening)', neg: 'Dovish (easing)' } },
  stress_score:    { label: 'Financial Stress',         descs: { pos: 'High market stress', neg: 'Calm markets' } },
};

export default function Macro() {
  const [regime, setRegime] = useState(null);
  const [briefing, setBriefing] = useState(null);
  const [history, setHistory] = useState([]);
  const [indicators, setIndicators] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);

  const load = useCallback(async (force = false) => {
    if (force) setLoading(true);
    try {
      const [r, b, h, ind] = await Promise.all([getRegime(), getBriefing(), getMacroHistory(), getIndicators()]);
      setRegime(r);
      setBriefing(b);
      setHistory(Array.isArray(h) ? h.slice(-30) : []);
      setIndicators(ind);
      setLastUpdated(new Date());
    } catch {}
    if (force) setLoading(false);
  }, []);

  useEffect(() => {
    load();
    const t1 = setInterval(() => load(), 300000);
    return () => clearInterval(t1);
  }, [load]);

  const handleRun = async () => {
    setRunning(true);
    try { await runMacroAgent(); await load(true); } catch {}
    setRunning(false);
  };

  const regimeKey = regime?.regime ?? briefing?.regime;
  const styles = REGIME_STYLES[regimeKey] || REGIME_STYLES['Transitional'];
  const guidance = REGIME_GUIDANCE[regimeKey];

  const fmtTime = (d) => {
    if (!d) return '—';
    const diff = Math.round((Date.now() - new Date(d)) / 60000);
    if (diff < 1) return 'just now';
    if (diff < 60) return `${diff} min ago`;
    return `${Math.round(diff / 60)} hr ago`;
  };

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">

      {/* Regime Banner */}
      <div className={`rounded-2xl p-6 text-white ${styles.bg}`}>
        <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
          <div>
            <p className="text-white/70 text-sm font-medium uppercase tracking-widest mb-1">Current Market Regime</p>
            <h1 className="text-4xl font-black">{regimeKey ?? 'Loading…'}</h1>
            <div className="flex gap-4 mt-2 text-sm text-white/80">
              {regime?.regime_confidence != null && <span>Confidence: <strong className="text-white">{regime.regime_confidence}/10</strong></span>}
              {regime?.regime_score != null && <span>Score: <strong className="text-white">{regime.regime_score}/100</strong></span>}
            </div>
          </div>
          <div className="text-right">
            <p className="text-white/60 text-xs">
              {lastUpdated ? `Updated ${fmtTime(lastUpdated)}` : 'Loading…'}
            </p>
            <div className="flex gap-2 mt-2 justify-end">
              <button
                onClick={() => load(true)}
                disabled={loading}
                className="px-3 py-1.5 text-xs bg-white/20 hover:bg-white/30 rounded-lg transition-colors"
              >
                {loading ? 'Refreshing…' : 'Refresh Data'}
              </button>
              <button
                onClick={handleRun}
                disabled={running}
                className="px-3 py-1.5 text-xs bg-white/90 text-gray-900 font-semibold hover:bg-white rounded-lg transition-colors disabled:opacity-50"
              >
                {running ? 'Running…' : 'Run Macro Agent'}
              </button>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {Object.entries(SUB_SCORE_META).map(([key, meta]) => {
            const val = regime?.[key] ?? briefing?.[key];
            const desc = val != null
              ? (val >= 0 ? meta.descs.pos : meta.descs.neg)
              : '';
            return <SubScoreBar key={key} label={meta.label} value={val} desc={desc} />;
          })}
        </div>
      </div>

      {/* Portfolio Guidance */}
      {guidance && (
        <div className={`rounded-xl border p-5 ${styles.light}`}>
          <h2 className={`text-sm font-bold uppercase tracking-wide mb-3 ${styles.text}`}>
            What This Regime Means for Your Portfolio
          </h2>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            {[
              { label: 'Gross Exposure Cap', value: guidance.gross },
              { label: 'Stop-Loss Mode', value: guidance.stops },
              { label: 'Recommended Sizing', value: guidance.sizing },
              { label: 'Guidance', value: guidance.note },
            ].map(({ label, value }) => (
              <div key={label}>
                <p className="text-xs text-gray-500 font-medium mb-0.5">{label}</p>
                <p className="text-gray-800 font-medium text-sm">{value}</p>
              </div>
            ))}
          </div>
          {briefing?.sector_tilts && Object.keys(briefing.sector_tilts).length > 0 && (
            <div className="mt-4 pt-4 border-t border-current/10">
              <p className="text-xs text-gray-500 font-medium mb-2">Sector Tilts</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(briefing.sector_tilts).map(([sector, tilt]) => (
                  <span key={sector} className={`text-xs px-2.5 py-1 rounded-full font-medium ${
                    tilt > 0 ? 'bg-green-100 text-green-700' : tilt < 0 ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-600'
                  }`}>
                    {sector} {tilt > 0 ? '▲' : tilt < 0 ? '▼' : ''}
                  </span>
                ))}
              </div>
            </div>
          )}
          {briefing?.portfolio_guidance && (
            <div className="mt-4 pt-4 border-t border-current/10">
              <p className="text-xs text-gray-500 font-medium mb-1">AI Guidance Note</p>
              <p className="text-sm text-gray-700 leading-relaxed">{briefing.portfolio_guidance}</p>
            </div>
          )}
        </div>
      )}

      {/* Regime History Sparkline */}
      {history.length > 1 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">Regime Score — Last 30 Briefings</h2>
          <ResponsiveContainer width="100%" height={80}>
            <LineChart data={history}>
              <Line type="monotone" dataKey="regime_score" stroke="#6366f1" strokeWidth={2} dot={false} />
              <RTooltip contentStyle={{ fontSize: 11, borderRadius: 6 }} formatter={(v) => [v, 'Regime Score']}
                labelFormatter={(_, pl) => pl?.[0]?.payload?.date ?? ''} />
            </LineChart>
          </ResponsiveContainer>
          {history.length > 0 && (() => {
            const last = history[history.length - 1];
            const count = history.filter(h => h.regime === last?.regime).reverse().findIndex(h => h.regime !== last?.regime);
            const streak = count === -1 ? history.length : count;
            return <p className="text-xs text-gray-400 mt-2">Regime has been <strong>{last?.regime}</strong> for at least {streak} consecutive sessions</p>;
          })()}
        </div>
      )}

      {/* Economic Indicators Grid */}
      {indicators && (
        <div>
          <h2 className="text-base font-semibold text-gray-900 mb-3">Economic Indicators</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {Object.entries(indicators).map(([key, data]) => {
              const meta = INDICATOR_LABELS[key] || { label: key, unit: '', desc: '' };
              const val = typeof data === 'object' ? (data?.value ?? data?.current) : data;
              const mom = typeof data === 'object' ? data?.mom_change : null;
              const yoy = typeof data === 'object' ? data?.yoy_change : null;
              if (val == null) return null;
              return (
                <div key={key} className="bg-white rounded-xl border border-gray-200 p-4" title={meta.desc}>
                  <div className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-1">{meta.label}</div>
                  <div className="text-2xl font-bold text-gray-900">
                    {typeof val === 'number' ? val.toFixed(2) : val}{meta.unit}
                  </div>
                  <div className="flex gap-3 mt-1">
                    {mom != null && (
                      <span className={`text-xs font-medium ${mom >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        {mom >= 0 ? '▲' : '▼'} {Math.abs(mom).toFixed(2)}% MoM
                      </span>
                    )}
                    {yoy != null && (
                      <span className={`text-xs font-medium ${yoy >= 0 ? 'text-green-600' : 'text-red-600'}`}>
                        {yoy >= 0 ? '▲' : '▼'} {Math.abs(yoy).toFixed(2)}% YoY
                      </span>
                    )}
                  </div>
                  {meta.desc && <p className="text-xs text-gray-400 mt-1 leading-snug">{meta.desc}</p>}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Upcoming Events */}
      {briefing?.upcoming_events?.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="text-base font-semibold text-gray-900 mb-3">Upcoming Events</h2>
          <div className="space-y-2">
            {briefing.upcoming_events.map((ev, i) => (
              <div key={i} className="flex items-start gap-3 text-sm">
                <span className="text-gray-400 w-24 flex-shrink-0 text-xs">{ev.date ?? '—'}</span>
                <span className="text-gray-700">{ev.event ?? ev}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Key Themes from briefing */}
      {briefing?.key_themes?.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-5">
          <h2 className="text-base font-semibold text-gray-900 mb-3">Key Market Themes</h2>
          <ul className="space-y-2">
            {briefing.key_themes.map((theme, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-gray-700">
                <span className="text-blue-400 mt-0.5">•</span>
                {theme}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
