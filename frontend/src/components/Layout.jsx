import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import { getRegime } from '../api/macro';
import { useSidebar } from '../context/SidebarContext';

const REGIME_CONFIG = {
  'Risk-On':      { dotClass: 'dot-green', colorVar: 'var(--regime-on-text)',  label: 'RISK-ON' },
  'Risk-Off':     { dotClass: 'dot-red',   colorVar: 'var(--regime-off-text)', label: 'RISK-OFF' },
  'Stagflation':  { dotClass: 'dot-amber', colorVar: 'var(--regime-st-text)',  label: 'STAGFLATION' },
  'Transitional': { dotClass: 'dot-blue',  colorVar: 'var(--regime-tr-text)',  label: 'TRANSITIONAL' },
};

function fmtAgo(d) {
  if (!d) return null;
  const diff = Math.round((Date.now() - d) / 1000);
  if (diff < 60)   return `${diff}s ago`;
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  return `${Math.round(diff / 3600)}h ago`;
}

export default function Layout() {
  const [regime, setRegime]           = useState(null);
  const [lastFetched, setLastFetched] = useState(null);
  const { collapsed }                 = useSidebar();

  useEffect(() => {
    const load = () =>
      getRegime()
        .then(r => { setRegime(r); setLastFetched(Date.now()); })
        .catch(() => {});
    load();
    const id = setInterval(load, 60_000);
    return () => clearInterval(id);
  }, []);

  const cfg = REGIME_CONFIG[regime?.regime] ?? null;

  return (
    <div className="min-h-screen" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <Sidebar />
      <div
        className={`${collapsed ? 'ml-[56px]' : 'ml-[212px]'} flex flex-col min-h-screen transition-all duration-200`}
      >
        {/* Regime status strip */}
        {regime && cfg && (
          <div
            className="flex items-center gap-3 px-5 py-1.5"
            style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg)' }}
          >
            <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${cfg.dotClass}`} />
            <span
              className="text-[10px] font-bold tracking-[0.12em]"
              style={{ fontFamily: 'Syne', color: cfg.colorVar }}
            >
              {cfg.label}
            </span>
            <span style={{ color: 'var(--border-2)' }}>·</span>
            <span className="text-[10px] font-data" style={{ color: 'var(--text-2)' }}>
              Confidence{' '}
              <span style={{ color: 'var(--text)' }}>
                {regime.regime_confidence != null ? Number(regime.regime_confidence).toFixed(1) : '—'}/10
              </span>
            </span>
            <span style={{ color: 'var(--border-2)' }}>·</span>
            <span className="text-[10px] font-data" style={{ color: 'var(--text-2)' }}>
              Score{' '}
              <span style={{ color: 'var(--text)' }}>
                {regime.regime_score != null ? Number(regime.regime_score).toFixed(1) : '—'}
              </span>
            </span>
            {lastFetched && (
              <span className="ml-auto text-[10px] font-data" style={{ color: 'var(--text-3)' }}>
                Updated {fmtAgo(lastFetched)}
              </span>
            )}
          </div>
        )}

        <main className="flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
