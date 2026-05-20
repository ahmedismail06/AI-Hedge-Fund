import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { getBriefing, getRegime, getMacroHistory, getIndicators, runMacroAgent } from '../api/macro';
import YieldCurveChart from '../components/YieldCurveChart';
import { LineChart, Line, Tooltip as RTooltip, ResponsiveContainer, ReferenceLine } from 'recharts';

const REGIME_STYLE = {
  'Risk-On':      { text: 'var(--green)',  bg: 'rgba(0,217,138,0.08)',  border: 'var(--green-border)'  },
  'Risk-Off':     { text: 'var(--red)',    bg: 'rgba(255,51,71,0.08)',   border: 'var(--red-border)'    },
  'Transitional': { text: 'var(--blue)',   bg: 'rgba(74,158,255,0.08)',  border: 'var(--blue-border)'   },
  'Stagflation':  { text: 'var(--amber)',  bg: 'rgba(255,179,0,0.08)',   border: 'var(--amber-border)'  },
};

const REGIME_GUIDANCE = {
  'Risk-On':      { gross: '150%', stops: '−8 / −15 / −20%',  sizing: 'Full sizing — Large/Medium/Small' },
  'Risk-Off':     { gross: '80%',  stops: '−5 / −10 / −15%',  sizing: 'Defensive — Small/Micro only'     },
  'Transitional': { gross: '120%', stops: '−8 / −15 / −20%',  sizing: 'Medium/Small preferred'           },
  'Stagflation':  { gross: '100%', stops: '−5 / −10 / −15%',  sizing: 'Cautious — Small/Micro only'      },
};

const INDICATOR_LABELS = {
  gdp_growth:       { label: 'GDP Growth',          unit: '%'    },
  cpi_yoy:          { label: 'CPI Inflation',        unit: '%'    },
  pce_yoy:          { label: 'PCE Inflation',        unit: '%'    },
  ppi_yoy:          { label: 'PPI',                  unit: '%'    },
  ism_pmi:          { label: 'ISM PMI',              unit: ''     },
  jobless_claims:   { label: 'Jobless Claims',       unit: 'K'    },
  nonfarm_payrolls: { label: 'Nonfarm Payrolls',     unit: 'K'    },
  breakeven_5y:     { label: '5Y Breakeven',         unit: '%'    },
  treasury_10y:     { label: '10Y Treasury',         unit: '%'    },
  treasury_2y:      { label: '2Y Treasury',          unit: '%'    },
  yield_curve:      { label: 'Yield Curve (10Y−2Y)', unit: 'bps'  },
  hy_spread:        { label: 'HY Credit Spread',     unit: 'bps'  },
  vix:              { label: 'VIX',                  unit: ''     },
  dxy:              { label: 'DXY',                  unit: ''     },
  spx_200dma_pct:   { label: 'SPX vs 200D MA',       unit: '%'    },
};

const SUB_SCORES = {
  growth_score:    'Growth',
  inflation_score: 'Inflation',
  fed_score:       'Fed Stance',
  stress_score:    'Stress',
};

function SL({ children }) {
  return (
    <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
      {children}
    </div>
  );
}

function Panel({ children, style = {} }) {
  return <div className="panel" style={style}>{children}</div>;
}

function PanelHeader({ label, title, action, onAction }) {
  return (
    <div className="panel-header">
      <div>
        <SL>{label}</SL>
        {title && <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text)', marginTop: '1px' }}>{title}</div>}
      </div>
      {action && (
        <button
          onClick={onAction}
          style={{ fontSize: '10px', fontWeight: 700, color: 'var(--accent)', fontFamily: 'Syne', background: 'none', border: 'none', cursor: 'pointer' }}
        >
          {action}
        </button>
      )}
    </div>
  );
}

function SubScorePill({ label, value }) {
  const isPos = (value ?? 0) >= 0;
  const color = isPos ? 'var(--green)' : 'var(--red)';
  const pct = Math.round(Math.abs(value ?? 0) * 50);
  return (
    <div>
      <div className="flex justify-between mb-1">
        <span style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'Syne' }}>{label}</span>
        <span style={{ fontSize: '10px', fontWeight: 700, fontFamily: 'JetBrains Mono', color }}>
          {value != null ? (isPos ? '+' : '') + value.toFixed(2) : '—'}
        </span>
      </div>
      <div className="relative h-1.5 rounded-full" style={{ background: 'var(--border)' }}>
        <div className="absolute top-0 left-1/2 w-px h-full" style={{ background: 'var(--text-3)' }} />
        <div
          className="absolute top-0 h-full rounded-full"
          style={{
            width: `${pct}%`,
            background: color,
            left: isPos ? '50%' : `calc(50% - ${pct}%)`,
          }}
        />
      </div>
    </div>
  );
}

function fmtAgo(d) {
  if (!d) return '—';
  const diff = Math.round((Date.now() - new Date(d)) / 60000);
  if (diff < 1) return 'just now';
  if (diff < 60) return `${diff}m ago`;
  if (diff < 1440) return `${Math.round(diff / 60)}h ago`;
  return `${Math.round(diff / 1440)}d ago`;
}

export default function Macro() {
  const { isAdmin } = useAuth();
  const [regime, setRegime]       = useState(null);
  const [briefing, setBriefing]   = useState(null);
  const [history, setHistory]     = useState([]);
  const [indicators, setIndicators] = useState(null);
  const [running, setRunning]     = useState(false);
  const [loading, setLoading]     = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);

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
    const t = setInterval(() => load(), 300_000);
    return () => clearInterval(t);
  }, [load]);

  const handleRun = async () => {
    setRunning(true);
    try { await runMacroAgent(); await load(true); } catch {}
    setRunning(false);
  };

  const regimeKey = regime?.regime ?? briefing?.regime ?? 'Transitional';
  const rs = REGIME_STYLE[regimeKey] || REGIME_STYLE['Transitional'];
  const guidance = REGIME_GUIDANCE[regimeKey];
  const confidence = regime?.regime_confidence;
  const confPct = confidence != null ? (confidence / 10) * 100 : null;

  const indicatorObj = indicators ?? {};

  const historyChart = history.map(h => ({
    date: h.date ? new Date(h.date).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '',
    score: h.regime_score ?? null,
  })).filter(d => d.score != null);

  return (
    <div className="p-4 animate-fade-in" style={{ maxWidth: '1600px', margin: '0 auto' }}>

      {/* Regime confidence header strip */}
      <div
        className="rounded-xl p-4 mb-4 flex flex-wrap items-center gap-6"
        style={{ background: rs.bg, border: `1px solid ${rs.border}` }}
      >
        <div>
          <SL>Current Regime</SL>
          <div style={{ fontSize: '28px', fontWeight: 900, color: rs.text, fontFamily: 'Syne', lineHeight: 1, marginTop: '4px' }}>
            {regimeKey}
          </div>
        </div>

        {confidence != null && (
          <div style={{ flex: 1, minWidth: '160px' }}>
            <div className="flex justify-between mb-1">
              <span style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'Syne', letterSpacing: '0.1em', textTransform: 'uppercase' }}>Confidence</span>
              <span style={{ fontSize: '12px', fontWeight: 700, fontFamily: 'JetBrains Mono', color: rs.text }}>{confidence}/10</span>
            </div>
            <div style={{ height: '6px', borderRadius: '3px', background: 'var(--border)' }}>
              <div style={{ width: `${confPct}%`, height: '100%', borderRadius: '3px', background: rs.text, transition: 'width 0.5s' }} />
            </div>
          </div>
        )}

        <div className="flex gap-4 flex-wrap" style={{ marginLeft: 'auto' }}>
          <div className="text-right">
            <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'Syne' }}>Updated</div>
            <div style={{ fontSize: '11px', fontFamily: 'JetBrains Mono', color: 'var(--text-2)' }}>{fmtAgo(lastUpdated)}</div>
          </div>
          <button
            onClick={() => load(true)}
            disabled={loading}
            style={{
              fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
              padding: '5px 10px', borderRadius: '6px',
              background: 'var(--surface)', color: 'var(--text-2)',
              border: '1px solid var(--border)', cursor: 'pointer',
              opacity: loading ? 0.5 : 1,
            }}
          >
            {loading ? '…' : 'Refresh'}
          </button>
          {isAdmin && (
            <button
              onClick={handleRun}
              disabled={running}
              style={{
                fontSize: '9px', fontWeight: 700, fontFamily: 'Syne',
                padding: '5px 10px', borderRadius: '6px',
                background: 'var(--accent)', color: '#000',
                border: 'none', cursor: running ? 'not-allowed' : 'pointer',
                opacity: running ? 0.6 : 1,
              }}
            >
              {running ? 'Running…' : 'Run Macro Agent'}
            </button>
          )}
        </div>
      </div>

      {/* Sub-scores strip */}
      {regime && (
        <Panel style={{ marginBottom: '12px' }}>
          <div className="p-4">
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px' }}>
              {Object.entries(SUB_SCORES).map(([key, label]) => (
                <SubScorePill key={key} label={label} value={regime[key] ?? briefing?.[key]} />
              ))}
            </div>
          </div>
        </Panel>
      )}

      {/* Three-column body */}
      <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr 280px', gap: '12px', alignItems: 'start' }}>

        {/* ── LEFT: Indicator grid ── */}
        <div className="space-y-2">
          <Panel>
            <PanelHeader label="Key Indicators" title="Macro Data" />
            <div className="p-3 space-y-2">
              {Object.entries(INDICATOR_LABELS).map(([key, meta]) => {
                const raw = indicatorObj[key];
                if (raw == null) return null;
                const val = typeof raw === 'object' ? (raw?.value ?? raw?.current ?? null) : raw;
                const mom = typeof raw === 'object' ? raw?.mom_change : null;
                if (val == null) return null;
                const numVal = typeof val === 'number' ? val : parseFloat(val);
                return (
                  <div
                    key={key}
                    style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '5px 0', borderBottom: '1px solid var(--border)' }}
                  >
                    <span style={{ fontSize: '10px', color: 'var(--text-3)', fontFamily: 'Syne' }}>{meta.label}</span>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      {mom != null && (
                        <span style={{ fontSize: '9px', color: mom >= 0 ? 'var(--green)' : 'var(--red)' }}>
                          {mom >= 0 ? '▲' : '▼'}
                        </span>
                      )}
                      <span style={{ fontSize: '12px', fontWeight: 700, fontFamily: 'JetBrains Mono', color: 'var(--text)' }}>
                        {isNaN(numVal) ? String(val) : numVal.toFixed(2)}{meta.unit}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </Panel>
        </div>

        {/* ── CENTER: Yield Curve + Regime History ── */}
        <div className="space-y-3">
          <Panel>
            <PanelHeader label="Fixed Income" title="US Treasury Yield Curve" />
            <div className="p-4">
              <YieldCurveChart indicators={indicatorObj} height={180} />
            </div>
          </Panel>

          {historyChart.length > 1 && (
            <Panel>
              <PanelHeader label="Regime History" title={`Last ${historyChart.length} sessions`} />
              <div className="p-4">
                <ResponsiveContainer width="100%" height={100}>
                  <LineChart data={historyChart}>
                    <Line type="monotone" dataKey="score" stroke="var(--accent)" strokeWidth={2} dot={false} isAnimationActive={false} />
                    <ReferenceLine y={50} stroke="var(--border)" strokeDasharray="3 3" />
                    <RTooltip
                      contentStyle={{ fontSize: 11, borderRadius: 6, background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }}
                      formatter={v => [v, 'Regime Score']}
                      labelFormatter={(_, pl) => pl?.[0]?.payload?.date ?? ''}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </Panel>
          )}

          {/* Key themes */}
          {briefing?.key_themes?.length > 0 && (
            <Panel>
              <PanelHeader label="Market Intelligence" title="Key Themes" />
              <div className="p-3">
                <ul className="space-y-2">
                  {briefing.key_themes.map((t, i) => (
                    <li key={i} style={{ fontSize: '12px', color: 'var(--text-2)', display: 'flex', gap: '8px' }}>
                      <span style={{ color: 'var(--accent)', flexShrink: 0 }}>•</span>
                      {t}
                    </li>
                  ))}
                </ul>
              </div>
            </Panel>
          )}
        </div>

        {/* ── RIGHT: Portfolio guidance + Upcoming events ── */}
        <div className="space-y-3">
          {guidance && (
            <Panel>
              <PanelHeader label="Portfolio Guidance" title={regimeKey} />
              <div className="p-3 space-y-3">
                {[
                  { label: 'Gross Cap',   value: guidance.gross },
                  { label: 'Stops',       value: guidance.stops },
                  { label: 'Sizing',      value: guidance.sizing },
                ].map(({ label, value }) => (
                  <div key={label} style={{ padding: '8px', borderRadius: '6px', background: 'var(--surface-2)' }}>
                    <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'Syne', letterSpacing: '0.1em', textTransform: 'uppercase' }}>{label}</div>
                    <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text)', marginTop: '2px' }}>{value}</div>
                  </div>
                ))}

                {briefing?.sector_tilts && Object.keys(briefing.sector_tilts).length > 0 && (
                  <div>
                    <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'Syne', letterSpacing: '0.1em', textTransform: 'uppercase', marginBottom: '6px' }}>
                      Sector Tilts
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {Object.entries(briefing.sector_tilts).map(([sector, tilt]) => (
                        <span
                          key={sector}
                          style={{
                            fontSize: '9px', padding: '2px 6px', borderRadius: '3px', fontFamily: 'Syne', fontWeight: 600,
                            ...(tilt > 0
                              ? { background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-border)' }
                              : tilt < 0
                                ? { background: 'var(--red-bg)', color: 'var(--red)', border: '1px solid var(--red-border)' }
                                : { background: 'var(--surface-2)', color: 'var(--text-3)', border: '1px solid var(--border)' }),
                          }}
                        >
                          {sector} {tilt > 0 ? '▲' : tilt < 0 ? '▼' : ''}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

                {briefing?.portfolio_guidance && (
                  <div style={{ fontSize: '11px', color: 'var(--text-2)', lineHeight: 1.5, paddingTop: '8px', borderTop: '1px solid var(--border)' }}>
                    {briefing.portfolio_guidance}
                  </div>
                )}
              </div>
            </Panel>
          )}

          {briefing?.upcoming_events?.length > 0 && (
            <Panel>
              <PanelHeader label="Calendar" title="Upcoming Events" />
              <div className="p-3">
                <div className="space-y-1">
                  {briefing.upcoming_events.map((ev, i) => (
                    <div key={i} style={{ display: 'flex', gap: '8px', padding: '5px 0', borderBottom: i < briefing.upcoming_events.length - 1 ? '1px solid var(--border)' : 'none' }}>
                      <span style={{ fontSize: '10px', fontFamily: 'JetBrains Mono', color: 'var(--text-3)', flexShrink: 0, minWidth: '56px' }}>
                        {ev.date ?? '—'}
                      </span>
                      <span style={{ fontSize: '11px', color: 'var(--text-2)' }}>{ev.event ?? ev}</span>
                    </div>
                  ))}
                </div>
              </div>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}
