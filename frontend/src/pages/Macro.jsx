import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { getBriefing, getRegime, getMacroHistory, getIndicators, runMacroAgent } from '../api/macro';
import { LineChart, Line, Tooltip as RTooltip, ResponsiveContainer } from 'recharts';

/* ─── Helper components ──────────────────────────────────────────── */
function SL({ children }) {
  return (
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
      {children}
    </div>
  );
}

function Card({ children, className = '', style = {} }) {
  return (
    <div className={`rounded-xl p-5 ${className}`} style={{ background: 'var(--surface)', border: '1px solid var(--border)', ...style }}>
      {children}
    </div>
  );
}

function CardHeader({ label, title, action, onAction }) {
  return (
    <div className="flex items-start justify-between mb-4">
      <div>
        <SL>{label}</SL>
        {title && <div className="text-[13px] font-semibold mt-0.5" style={{ color: 'var(--text)' }}>{title}</div>}
      </div>
      {action && (
        <button onClick={onAction} className="text-[11px] font-bold transition-opacity hover:opacity-70" style={{ color: 'var(--accent)', fontFamily: 'Syne' }}>
          {action}
        </button>
      )}
    </div>
  );
}

/* ─── Regime config ──────────────────────────────────────────────── */
const REGIME_BANNER_STYLE = {
  'Risk-On':     { bg: 'rgba(0, 217, 138, 0.12)',  border: 'var(--regime-on-border)',  text: 'var(--regime-on-text)'  },
  'Risk-Off':    { bg: 'rgba(255, 51, 71, 0.12)',   border: 'var(--regime-off-border)', text: 'var(--regime-off-text)' },
  'Transitional':{ bg: 'rgba(74, 158, 255, 0.12)',  border: 'var(--regime-tr-border)',  text: 'var(--regime-tr-text)'  },
  'Stagflation': { bg: 'rgba(255, 179, 0, 0.12)',   border: 'var(--regime-st-border)',  text: 'var(--regime-st-text)'  },
};

const REGIME_GUIDANCE_STYLE = {
  'Risk-On':     { bg: 'var(--regime-on-bg)',  border: 'var(--regime-on-border)',  text: 'var(--regime-on-text)'  },
  'Risk-Off':    { bg: 'var(--regime-off-bg)', border: 'var(--regime-off-border)', text: 'var(--regime-off-text)' },
  'Transitional':{ bg: 'var(--regime-tr-bg)',  border: 'var(--regime-tr-border)',  text: 'var(--regime-tr-text)'  },
  'Stagflation': { bg: 'var(--regime-st-bg)',  border: 'var(--regime-st-border)',  text: 'var(--regime-st-text)'  },
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

/* ─── SubScoreBar ────────────────────────────────────────────────── */
function SubScoreBar({ label, value, desc, bannerText }) {
  const pct = Math.round(((value ?? 0) + 1) / 2 * 100); // map -1..+1 → 0..100%
  const isPos = (value ?? 0) >= 0;
  const fillColor = isPos ? 'var(--green)' : 'var(--red)';
  const valueColor = isPos ? 'var(--green)' : 'var(--red)';

  return (
    <div className="min-w-0">
      <div className="flex justify-between items-baseline mb-1">
        <span className="text-xs font-medium" style={{ color: 'var(--text-2)', fontFamily: 'Syne' }}>{label}</span>
        <span className="text-sm font-bold" style={{ color: valueColor, fontFamily: 'JetBrains Mono' }}>
          {value != null ? (value >= 0 ? '+' : '') + value.toFixed(2) : '—'}
        </span>
      </div>
      <div className="relative h-2 rounded-full" style={{ background: 'var(--border)' }}>
        <div className="absolute top-0 left-1/2 w-0.5 h-full" style={{ background: 'var(--text-3)' }} />
        <div
          className="absolute top-0 h-full rounded-full transition-all"
          style={{
            backgroundColor: fillColor,
            width: `${Math.abs((value ?? 0)) * 50}%`,
            left: isPos ? '50%' : `calc(50% - ${Math.abs(value ?? 0) * 50}%)`,
          }}
        />
      </div>
      {desc && <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>{desc}</p>}
    </div>
  );
}

const SUB_SCORE_META = {
  growth_score:    { label: 'Growth Outlook',           descs: { pos: 'Strong growth signals', neg: 'Slowing growth' } },
  inflation_score: { label: 'Inflation Pressure',       descs: { pos: 'High / rising inflation', neg: 'Benign inflation' } },
  fed_score:       { label: 'Federal Reserve Stance',   descs: { pos: 'Hawkish (tightening)', neg: 'Dovish (easing)' } },
  stress_score:    { label: 'Financial Stress',         descs: { pos: 'High market stress', neg: 'Calm markets' } },
};

/* ─── Component ──────────────────────────────────────────────────── */
export default function Macro() {
  const { isAdmin } = useAuth();
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
  const bannerStyle = REGIME_BANNER_STYLE[regimeKey] || REGIME_BANNER_STYLE['Transitional'];
  const guidanceStyle = REGIME_GUIDANCE_STYLE[regimeKey] || REGIME_GUIDANCE_STYLE['Transitional'];
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

      {/* ── Regime Banner ── */}
      <div
        className="rounded-2xl p-6"
        style={{ background: bannerStyle.bg, border: `1px solid ${bannerStyle.border}` }}
      >
        <div className="flex flex-wrap items-start justify-between gap-4 mb-5">
          <div>
            <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne', marginBottom: '4px' }}>
              Current Market Regime
            </div>
            <h1
              className="text-4xl font-black"
              style={{ color: bannerStyle.text, fontFamily: 'Syne' }}
            >
              {regimeKey ?? 'Loading…'}
            </h1>
            <div className="flex gap-4 mt-2 text-sm" style={{ color: 'var(--text-2)' }}>
              {regime?.regime_confidence != null && (
                <span>Confidence: <strong style={{ color: bannerStyle.text, fontFamily: 'JetBrains Mono' }}>{regime.regime_confidence}/10</strong></span>
              )}
              {regime?.regime_score != null && (
                <span>Score: <strong style={{ color: bannerStyle.text, fontFamily: 'JetBrains Mono' }}>{regime.regime_score}/100</strong></span>
              )}
            </div>
          </div>
          <div className="text-right">
            <p className="text-xs" style={{ color: 'var(--text-3)' }}>
              {lastUpdated ? `Updated ${fmtTime(lastUpdated)}` : 'Loading…'}
            </p>
            <div className="flex gap-2 mt-2 justify-end">
              <button
                onClick={() => load(true)}
                disabled={loading}
                className="px-3 py-1.5 text-xs rounded-lg transition-opacity hover:opacity-80 font-semibold"
                style={{ background: 'var(--surface-2)', color: 'var(--text-2)', border: '1px solid var(--border)', fontFamily: 'Syne' }}
              >
                {loading ? 'Refreshing…' : 'Refresh Data'}
              </button>
              <button
                onClick={handleRun}
                disabled={running || !isAdmin}
                title={!isAdmin ? 'Guest mode — read only' : undefined}
                className="px-3 py-1.5 text-xs font-bold rounded-lg transition-opacity hover:opacity-80 disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ background: 'var(--accent)', color: '#000', fontFamily: 'Syne' }}
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
            return <SubScoreBar key={key} label={meta.label} value={val} desc={desc} bannerText={bannerStyle.text} />;
          })}
        </div>
      </div>

      {/* ── Portfolio Guidance ── */}
      {guidance && (
        <div
          className="rounded-xl p-5"
          style={{ background: guidanceStyle.bg, border: `1px solid ${guidanceStyle.border}` }}
        >
          <div
            className="text-sm font-bold uppercase tracking-wide mb-3"
            style={{ color: guidanceStyle.text, fontFamily: 'Syne', letterSpacing: '0.1em' }}
          >
            What This Regime Means for Your Portfolio
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            {[
              { label: 'Gross Exposure Cap', value: guidance.gross },
              { label: 'Stop-Loss Mode', value: guidance.stops },
              { label: 'Recommended Sizing', value: guidance.sizing },
              { label: 'Guidance', value: guidance.note },
            ].map(({ label, value }) => (
              <div key={label}>
                <p className="text-xs font-medium mb-0.5" style={{ color: 'var(--text-3)', fontFamily: 'Syne' }}>{label}</p>
                <p className="font-medium text-sm" style={{ color: 'var(--text)' }}>{value}</p>
              </div>
            ))}
          </div>

          {briefing?.sector_tilts && Object.keys(briefing.sector_tilts).length > 0 && (
            <div className="mt-4 pt-4" style={{ borderTop: '1px solid var(--border)' }}>
              <p className="text-xs font-medium mb-2" style={{ color: 'var(--text-3)', fontFamily: 'Syne' }}>Sector Tilts</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(briefing.sector_tilts).map(([sector, tilt]) => (
                  <span
                    key={sector}
                    className="text-xs px-2.5 py-1 rounded-full font-medium"
                    style={
                      tilt > 0
                        ? { background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-border)' }
                        : tilt < 0
                          ? { background: 'var(--red-bg)', color: 'var(--red)', border: '1px solid var(--red-border)' }
                          : { background: 'var(--surface-2)', color: 'var(--text-3)', border: '1px solid var(--border)' }
                    }
                  >
                    {sector} {tilt > 0 ? '▲' : tilt < 0 ? '▼' : ''}
                  </span>
                ))}
              </div>
            </div>
          )}

          {briefing?.portfolio_guidance && (
            <div className="mt-4 pt-4" style={{ borderTop: '1px solid var(--border)' }}>
              <p className="text-xs font-medium mb-1" style={{ color: 'var(--text-3)', fontFamily: 'Syne' }}>AI Guidance Note</p>
              <p className="text-sm leading-relaxed" style={{ color: 'var(--text-2)' }}>{briefing.portfolio_guidance}</p>
            </div>
          )}
        </div>
      )}

      {/* ── Regime History Sparkline ── */}
      {history.length > 1 && (
        <div className="rounded-xl p-5" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
          <div className="mb-3">
            <SL>Regime Score History</SL>
            <div className="text-[13px] font-semibold mt-0.5" style={{ color: 'var(--text)' }}>Last 30 Briefings</div>
          </div>
          <ResponsiveContainer width="100%" height={80}>
            <LineChart data={history}>
              <Line type="monotone" dataKey="regime_score" stroke="var(--accent)" strokeWidth={2} dot={false} />
              <RTooltip
                contentStyle={{ fontSize: 11, borderRadius: 6, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }}
                formatter={(v) => [v, 'Regime Score']}
                labelFormatter={(_, pl) => pl?.[0]?.payload?.date ?? ''}
              />
            </LineChart>
          </ResponsiveContainer>
          {history.length > 0 && (() => {
            const last = history[history.length - 1];
            const count = history.filter(h => h.regime === last?.regime).reverse().findIndex(h => h.regime !== last?.regime);
            const streak = count === -1 ? history.length : count;
            return (
              <p className="text-xs mt-2" style={{ color: 'var(--text-3)' }}>
                Regime has been <strong style={{ color: 'var(--text-2)' }}>{last?.regime}</strong> for at least {streak} consecutive sessions
              </p>
            );
          })()}
        </div>
      )}

      {/* ── Economic Indicators Grid ── */}
      {indicators && (
        <div>
          <div className="mb-3">
            <SL>Economic Indicators</SL>
            <div className="text-base font-semibold mt-0.5" style={{ color: 'var(--text)', fontFamily: 'Syne' }}>Key Macro Data</div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {Object.entries(indicators).map(([key, data]) => {
              const meta = INDICATOR_LABELS[key] || { label: key, unit: '', desc: '' };
              const val = typeof data === 'object' ? (data?.value ?? data?.current) : data;
              const mom = typeof data === 'object' ? data?.mom_change : null;
              const yoy = typeof data === 'object' ? data?.yoy_change : null;
              if (val == null) return null;
              return (
                <div
                  key={key}
                  className="rounded-xl p-4"
                  style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
                  title={meta.desc}
                >
                  <SL>{meta.label}</SL>
                  <div
                    className="text-2xl font-bold mt-1"
                    style={{ color: 'var(--text)', fontFamily: 'JetBrains Mono' }}
                  >
                    {typeof val === 'number' ? val.toFixed(2) : val}{meta.unit}
                  </div>
                  <div className="flex gap-3 mt-1">
                    {mom != null && (
                      <span
                        className="text-xs font-medium"
                        style={{ color: mom >= 0 ? 'var(--green)' : 'var(--red)' }}
                      >
                        {mom >= 0 ? '▲' : '▼'} {Math.abs(mom).toFixed(2)}% MoM
                      </span>
                    )}
                    {yoy != null && (
                      <span
                        className="text-xs font-medium"
                        style={{ color: yoy >= 0 ? 'var(--green)' : 'var(--red)' }}
                      >
                        {yoy >= 0 ? '▲' : '▼'} {Math.abs(yoy).toFixed(2)}% YoY
                      </span>
                    )}
                  </div>
                  {meta.desc && (
                    <p className="text-xs mt-1 leading-snug" style={{ color: 'var(--text-3)' }}>{meta.desc}</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Upcoming Events ── */}
      {briefing?.upcoming_events?.length > 0 && (
        <div className="rounded-xl p-5" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
          <div className="mb-3">
            <SL>Calendar</SL>
            <div className="text-base font-semibold mt-0.5" style={{ color: 'var(--text)', fontFamily: 'Syne' }}>Upcoming Events</div>
          </div>
          <div className="space-y-2">
            {briefing.upcoming_events.map((ev, i) => (
              <div
                key={i}
                className="flex items-start gap-3 text-sm py-1.5"
                style={{ borderBottom: i < briefing.upcoming_events.length - 1 ? '1px solid var(--border)' : 'none' }}
                onMouseEnter={e => e.currentTarget.style.background = 'var(--surface-2)'}
                onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
              >
                <span
                  className="w-24 flex-shrink-0 text-xs"
                  style={{ color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}
                >
                  {ev.date ?? '—'}
                </span>
                <span style={{ color: 'var(--text-2)' }}>{ev.event ?? ev}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Key Themes ── */}
      {briefing?.key_themes?.length > 0 && (
        <div className="rounded-xl p-5" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
          <div className="mb-3">
            <SL>Market Intelligence</SL>
            <div className="text-base font-semibold mt-0.5" style={{ color: 'var(--text)', fontFamily: 'Syne' }}>Key Market Themes</div>
          </div>
          <ul className="space-y-2">
            {briefing.key_themes.map((theme, i) => (
              <li key={i} className="flex items-start gap-2 text-sm" style={{ color: 'var(--text-2)' }}>
                <span className="mt-0.5" style={{ color: 'var(--accent)' }}>•</span>
                {theme}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
