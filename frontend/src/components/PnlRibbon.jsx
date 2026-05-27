import { useState, useEffect } from 'react';
import { getExecutionStatus } from '../api/execution';
import { getExposure } from '../api/portfolio';
import { getRegime } from '../api/macro';

const REGIME_CONFIG = {
  'Risk-On':      { dotClass: 'dot-green', colorVar: 'var(--regime-on-text)',  label: 'RISK-ON' },
  'Constructive': { dotClass: 'dot-green', colorVar: 'var(--regime-on-text)',  label: 'CONSTRUCTIVE' },
  'Risk-Off':     { dotClass: 'dot-red',   colorVar: 'var(--regime-off-text)', label: 'RISK-OFF' },
  'Stagflation':  { dotClass: 'dot-amber', colorVar: 'var(--regime-st-text)',  label: 'STAGFLATION' },
  'Transitional': { dotClass: 'dot-blue',  colorVar: 'var(--regime-tr-text)',  label: 'TRANSITIONAL' },
};

function fmt$(v) {
  if (v == null) return '—';
  const abs    = Math.abs(v);
  const prefix = v < 0 ? '-' : '';
  if (abs >= 1_000_000) return `${prefix}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000)     return `${prefix}$${(abs / 1_000).toFixed(1)}k`;
  return `${prefix}$${abs.toFixed(2)}`;
}

function Divider() {
  return <div className="flex-shrink-0 w-px mx-3 self-stretch" style={{ background: 'var(--border)' }} />;
}

function RibbonStat({ label, value, valueColor, sub }) {
  return (
    <div className="flex-shrink-0 flex flex-col justify-center px-3 py-1.5 gap-0.5">
      <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
        {label}
      </div>
      <div style={{ fontFamily: 'JetBrains Mono', fontSize: '13px', fontWeight: 700, color: valueColor ?? 'var(--text)', lineHeight: 1 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'JetBrains Mono' }}>{sub}</div>}
    </div>
  );
}

export default function PnlRibbon() {
  const [exec,    setExec]    = useState(null);
  const [exposure, setExposure] = useState(null);
  const [regime,  setRegime]  = useState(null);

  useEffect(() => {
    const load = () => {
      getExecutionStatus().then(setExec).catch(() => {});
      getExposure().then(setExposure).catch(() => {});
      getRegime().then(setRegime).catch(() => {});
    };
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  const nav        = exec?.net_liquidation ?? null;
  const dayPnl     = exec?.unrealized_pnl  ?? null;
  const realised   = exec?.realized_pnl    ?? null;
  const cash       = exec?.cash            ?? null;
  const ibkrOk     = exec?.ibkr_connected  === true;
  const isPaper    = exec?.is_paper        === true;

  const gross      = exposure?.gross_exposure_pct ?? null;
  const net        = exposure?.net_exposure_pct   ?? null;

  const regimeKey  = regime?.regime ?? null;
  const regimeCfg  = REGIME_CONFIG[regimeKey] ?? null;
  const confidence = regime?.regime_confidence != null ? Number(regime.regime_confidence).toFixed(1) : null;

  const pnlColor = (v) => v == null ? 'var(--text)' : v >= 0 ? 'var(--green)' : 'var(--red)';

  return (
    <div className="ribbon-strip fixed left-0 right-0 z-40 overflow-x-auto no-scrollbar" style={{ top: '48px' }}>
      {/* NAV — largest number in ribbon */}
      <div className="flex-shrink-0 flex items-center gap-2 px-4 py-1.5">
        <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>NAV</div>
        <div style={{ fontFamily: 'JetBrains Mono', fontSize: '16px', fontWeight: 800, color: 'var(--text)', lineHeight: 1 }}>
          {nav != null ? fmt$(nav) : '—'}
        </div>
        {/* IBKR status dot */}
        <span
          className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${ibkrOk ? 'dot-green' : 'dot-red'}`}
          title={ibkrOk ? (isPaper ? 'IBKR Paper' : 'IBKR Live') : 'IBKR Disconnected'}
        />
        <span style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'Syne', fontWeight: 600 }}>
          {ibkrOk ? (isPaper ? 'PAPER' : 'LIVE') : 'DISC.'}
        </span>
      </div>

      <Divider />

      <RibbonStat
        label="Unrealized P&L"
        value={dayPnl != null ? `${dayPnl >= 0 ? '+' : ''}${fmt$(dayPnl)}` : '—'}
        valueColor={pnlColor(dayPnl)}
      />

      <RibbonStat
        label="Realized P&L"
        value={realised != null ? `${realised >= 0 ? '+' : ''}${fmt$(realised)}` : '—'}
        valueColor={pnlColor(realised)}
      />

      <div className="ribbon-stat-hide-mobile flex-shrink-0">
        <RibbonStat label="Cash" value={fmt$(cash)} />
      </div>

      <Divider />

      <RibbonStat
        label="Gross"
        value={gross != null ? `${gross.toFixed(1)}%` : '—'}
        valueColor={gross != null && gross > 150 ? 'var(--red)' : 'var(--text)'}
      />
      <div className="ribbon-stat-hide-mobile flex-shrink-0">
        <RibbonStat
          label="Net"
          value={net != null ? `${net >= 0 ? '+' : ''}${net.toFixed(1)}%` : '—'}
        />
      </div>

      <Divider />

      {/* Regime */}
      {regimeCfg ? (
        <div className="flex-shrink-0 flex items-center gap-2 px-3 py-1.5">
          <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${regimeCfg.dotClass}`} />
          <div>
            <div style={{ fontSize: '9px', fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-3)', fontFamily: 'Syne' }}>
              Regime
            </div>
            <div style={{ fontFamily: 'Syne', fontSize: '11px', fontWeight: 800, color: regimeCfg.colorVar, lineHeight: 1 }}>
              {regimeCfg.label}
              {confidence && (
                <span style={{ fontWeight: 400, color: 'var(--text-3)', fontSize: '10px', marginLeft: '5px', fontFamily: 'JetBrains Mono' }}>
                  {confidence}/10
                </span>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div className="flex-shrink-0 px-3 py-1.5">
          <div style={{ fontSize: '9px', color: 'var(--text-3)', fontFamily: 'Syne' }}>No regime</div>
        </div>
      )}
    </div>
  );
}
