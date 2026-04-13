import { useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import { getRegime } from '../api/macro';
import { useSidebar } from '../context/SidebarContext';
import { useTheme } from '../context/ThemeContext';

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
  const { collapsed, setMobileOpen }  = useSidebar();
  const { theme, toggle: toggleTheme } = useTheme();
  const isDark = theme === 'dark';

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

      {/* ── Mobile header bar ─────────────────────────── */}
      <header
        className="md:hidden fixed top-0 left-0 right-0 z-30 flex items-center gap-3 px-4"
        style={{
          height:       '52px',
          background:   'var(--sidebar-bg)',
          borderBottom: '1px solid var(--sidebar-border)',
        }}
      >
        {/* Hamburger */}
        <button
          onClick={() => setMobileOpen(true)}
          className="w-9 h-9 flex items-center justify-center rounded-md flex-shrink-0"
          style={{ color: 'var(--text-2)' }}
          aria-label="Open menu"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '22px' }}>menu</span>
        </button>

        {/* Logo */}
        <div className="flex-1 min-w-0">
          <div className="text-[14px] font-bold tracking-tight truncate" style={{ fontFamily: 'Syne', color: 'var(--text)' }}>
            Precision Ledger
          </div>
        </div>

        {/* Regime dot (if available) */}
        {cfg && (
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${cfg.dotClass}`} />
        )}

        {/* Theme toggle */}
        <button
          onClick={toggleTheme}
          className="w-9 h-9 flex items-center justify-center rounded-md flex-shrink-0"
          style={{ color: 'var(--text-2)' }}
          aria-label="Toggle theme"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>
            {isDark ? 'light_mode' : 'dark_mode'}
          </span>
        </button>
      </header>

      <div
        className={`
          ${collapsed ? 'md:ml-[56px]' : 'md:ml-[212px]'}
          flex flex-col min-h-screen transition-all duration-200
          pt-[52px] md:pt-0
        `}
      >
        {/* Regime status strip — desktop only */}
        {regime && cfg && (
          <div
            className="hidden md:flex items-center gap-3 px-5 py-1.5"
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
