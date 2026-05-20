import { NavLink, useNavigate } from 'react-router-dom';
import { useTheme } from '../context/ThemeContext';
import { useAuth } from '../context/AuthContext';
import { useState, useEffect } from 'react';
import { getPMStatus } from '../api/pm';
import { getCriticalAlerts } from '../api/risk';
import { getPending } from '../api/portfolio';
import ConfirmDialog from './ConfirmDialog';
import { haltPM, resumePM } from '../api/pm';

const NAV_ITEMS = [
  { to: '/',          label: 'Command Center', icon: 'dashboard',          end: true },
  { to: '/book',      label: 'Book',           icon: 'account_balance_wallet' },
  { to: '/risk',      label: 'Risk',           icon: 'security' },
  { to: '/signals',   label: 'Signals',        icon: 'filter_list' },
  { to: '/macro',     label: 'Macro',          icon: 'language' },
  { to: '/execution', label: 'Execution',      icon: 'bolt' },
];

export default function TopNav({ mobileOpen, setMobileOpen }) {
  const { theme, toggle: toggleTheme } = useTheme();
  const { isAdmin, role, logout } = useAuth();
  const navigate = useNavigate();
  const isDark = theme === 'dark';
  const [pmStatus, setPmStatus]         = useState(null);
  const [criticalCount, setCriticalCount] = useState(0);
  const [pendingCount, setPendingCount]   = useState(0);
  const [showConfirm, setShowConfirm]     = useState(false);
  const [toggling, setToggling]           = useState(false);

  useEffect(() => {
    const load = () => {
      getPMStatus().then(r => setPmStatus(r)).catch(() => {});
      getCriticalAlerts().then(r => setCriticalCount(Array.isArray(r) ? r.length : 0)).catch(() => {});
      getPending().then(r => setPendingCount(Array.isArray(r) ? r.length : 0)).catch(() => {});
    };
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  const isHalted = pmStatus?.daily_loss_halt_triggered ?? false;
  const mode = pmStatus?.mode ?? 'supervised';
  const isAutonomous = mode === 'autonomous';

  const handleToggle = async () => {
    setToggling(true);
    setShowConfirm(false);
    try {
      if (isHalted) { await resumePM(); } else { await haltPM(); }
      getPMStatus().then(r => setPmStatus(r)).catch(() => {});
    } catch {}
    finally { setToggling(false); }
  };

  return (
    <>
      {showConfirm && (
        <ConfirmDialog
          title={isHalted ? 'Resume PM Agent?' : 'Halt new entries?'}
          message={isHalted
            ? 'The AI PM will resume opening new positions.'
            : 'The PM will stop opening new positions. Existing positions continue to be monitored.'}
          confirmLabel={isHalted ? 'Resume' : 'Halt New Entries'}
          destructive={!isHalted}
          onConfirm={handleToggle}
          onCancel={() => setShowConfirm(false)}
        />
      )}

      {/* ── Mobile backdrop */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 md:hidden"
          style={{ background: 'var(--modal-backdrop)' }}
          onClick={() => setMobileOpen(false)}
        />
      )}

      <header
        className="fixed top-0 left-0 right-0 z-50 flex items-center gap-1 px-4"
        style={{
          height: '48px',
          background: 'var(--sidebar-bg)',
          borderBottom: '1px solid var(--border)',
          fontFamily: 'Syne',
        }}
      >
        {/* Mobile hamburger */}
        <button
          className="md:hidden w-8 h-8 flex items-center justify-center rounded-md flex-shrink-0 mr-1"
          style={{ color: 'var(--text-2)' }}
          onClick={() => setMobileOpen(v => !v)}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '20px' }}>
            {mobileOpen ? 'close' : 'menu'}
          </span>
        </button>

        {/* Logo */}
        <div className="flex-shrink-0 mr-4 hidden md:flex items-center gap-2">
          <div
            className="w-6 h-6 rounded flex items-center justify-center text-[10px] font-bold"
            style={{ background: 'var(--accent-muted)', color: 'var(--accent)', border: '1px solid var(--accent-ring)', fontFamily: 'JetBrains Mono' }}
          >
            PL
          </div>
          <div className="text-[13px] font-bold tracking-tight" style={{ color: 'var(--text)' }}>
            Precision Ledger
          </div>
        </div>

        {/* Nav links — desktop */}
        <nav className="hidden md:flex items-center gap-0.5 flex-1 overflow-x-auto no-scrollbar">
          {NAV_ITEMS.map(({ to, label, icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => `nav-top-link${isActive ? ' active' : ''}`}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>{icon}</span>
              {label}
              {label === 'Risk' && criticalCount > 0 && (
                <span
                  className="text-[9px] font-bold px-1 rounded"
                  style={{ background: 'var(--red-bg)', color: 'var(--red)', border: '1px solid var(--red-border)' }}
                >
                  {criticalCount}
                </span>
              )}
              {label === 'Book' && pendingCount > 0 && (
                <span
                  className="text-[9px] font-bold px-1 rounded"
                  style={{ background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-border)' }}
                >
                  {pendingCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Right controls */}
        <div className="flex items-center gap-2 ml-auto flex-shrink-0">
          {/* PM mode badge — desktop */}
          <button
            className="hidden md:block text-[10px] font-bold tracking-[0.1em] uppercase px-2.5 py-1 rounded-md"
            onClick={() => isAdmin && !toggling && setShowConfirm(true)}
            disabled={!isAdmin || toggling}
            title={!isAdmin ? 'Guest mode — read only' : isHalted ? 'Click to resume' : 'Click to halt'}
            style={{
              cursor: !isAdmin ? 'default' : 'pointer',
              ...(isHalted
                ? { background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-border)' }
                : isAutonomous
                ? { background: 'var(--accent-muted)', color: 'var(--accent)', border: '1px solid var(--accent-ring)' }
                : { background: 'var(--surface-2)', color: 'var(--text-2)', border: '1px solid var(--border)' }),
              transition: 'all 0.15s',
              opacity: toggling ? 0.6 : 1,
            }}
          >
            {isHalted ? 'HALTED' : mode.toUpperCase()}
          </button>

          {/* Role badge */}
          <span
            className="hidden md:block text-[9px] font-bold tracking-[0.12em] uppercase px-1.5 py-0.5 rounded"
            style={
              role === 'admin'
                ? { background: 'var(--green-bg)', color: 'var(--green)', border: '1px solid var(--green-border)' }
                : { background: 'var(--surface-2)', color: 'var(--text-3)', border: '1px solid var(--border)' }
            }
          >
            {role === 'admin' ? 'Admin' : 'Guest'}
          </span>

          {/* Theme toggle */}
          <button
            onClick={toggleTheme}
            className="w-8 h-8 flex items-center justify-center rounded-md"
            style={{ color: 'var(--text-2)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--accent)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-2)'}
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
              {isDark ? 'light_mode' : 'dark_mode'}
            </span>
          </button>

          {/* Logout */}
          <button
            onClick={() => { logout(); navigate('/login'); }}
            className="w-8 h-8 flex items-center justify-center rounded-md"
            style={{ color: 'var(--text-2)' }}
            onMouseEnter={e => e.currentTarget.style.color = 'var(--red)'}
            onMouseLeave={e => e.currentTarget.style.color = 'var(--text-2)'}
            title="Logout"
          >
            <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>logout</span>
          </button>
        </div>
      </header>

      {/* Mobile nav drawer */}
      {mobileOpen && (
        <div
          className="fixed top-[48px] left-0 right-0 z-40 md:hidden"
          style={{ background: 'var(--sidebar-bg)', borderBottom: '1px solid var(--border)' }}
        >
          {NAV_ITEMS.map(({ to, label, icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              onClick={() => setMobileOpen(false)}
              className={({ isActive }) =>
                `flex items-center gap-3 px-5 py-3 text-[12px] font-bold uppercase tracking-wide ${isActive ? 'text-amber-500' : ''}`
              }
              style={({ isActive }) => ({
                fontFamily: 'Syne',
                color: isActive ? 'var(--accent)' : 'var(--text-2)',
                background: isActive ? 'var(--accent-muted)' : 'transparent',
                borderBottom: '1px solid var(--border)',
              })}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>{icon}</span>
              {label}
            </NavLink>
          ))}
        </div>
      )}
    </>
  );
}
