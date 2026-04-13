import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { getPMStatus, haltPM, resumePM } from '../api/pm';
import { getCriticalAlerts, getAlerts } from '../api/risk';
import { getExecutionStatus } from '../api/execution';
import { getPending } from '../api/portfolio';
import { useSidebar } from '../context/SidebarContext';
import { useTheme } from '../context/ThemeContext';
import ConfirmDialog from './ConfirmDialog';
import RiskAlert from './RiskAlert';

const NAV_ITEMS = [
  { to: '/',             label: 'Dashboard',    icon: 'dashboard' },
  { to: '/portfolio',    label: 'Portfolio',    icon: 'account_balance_wallet' },
  { to: '/execution',    label: 'Execution',    icon: 'bolt' },
  { to: '/research',     label: 'Research',     icon: 'query_stats' },
  { to: '/screener',     label: 'Screener',     icon: 'filter_list' },
  { to: '/macro',        label: 'Macro',        icon: 'language' },
  { to: '/risk',         label: 'Risk',         icon: 'security' },
  { to: '/orchestrator', label: 'Orchestrator', icon: 'memory' },
];

export default function Sidebar() {
  const { collapsed, setCollapsed, mobileOpen, setMobileOpen } = useSidebar();
  const { theme, toggle: toggleTheme } = useTheme();
  const [status, setStatus]             = useState(null);
  const [showConfirm, setShowConfirm]   = useState(false);
  const [toggling, setToggling]         = useState(false);
  const [notifOpen, setNotifOpen]       = useState(false);
  const [recentAlerts, setRecentAlerts] = useState([]);
  const [ibkrOk, setIbkrOk]            = useState(null);
  const [pendingCount, setPendingCount] = useState(0);

  const loadStatus = () =>
    getPMStatus().then(r => setStatus(r)).catch(() => {});

  const loadHealth = () => {
    getCriticalAlerts().catch(() => {});
    getExecutionStatus()
      .then(r => {
        const d = r?.data || r || {};
        setIbkrOk(d.connected ?? d.ibkr_connected ?? null);
      })
      .catch(() => {});
    getPending()
      .then(r => setPendingCount(Array.isArray(r) ? r.length : 0))
      .catch(() => {});
  };

  const loadAlerts = () =>
    getAlerts().then(r => setRecentAlerts(Array.isArray(r) ? r.slice(0, 10) : [])).catch(() => {});

  useEffect(() => {
    loadStatus(); loadHealth(); loadAlerts();
    const id1 = setInterval(loadStatus, 30000);
    const id2 = setInterval(loadHealth, 60000);
    return () => { clearInterval(id1); clearInterval(id2); };
  }, []);

  const mode          = status?.mode ?? 'autonomous';
  const isHalted      = status?.daily_loss_halt_triggered ?? false;
  const criticalCount = status?.active_critical_alerts ?? 0;
  const isAutonomous  = mode === 'autonomous';
  const isDark        = theme === 'dark';

  const handleConfirmToggle = async () => {
    setToggling(true);
    setShowConfirm(false);
    try {
      if (isHalted) { await resumePM(); } else { await haltPM(); }
      loadStatus();
    } catch {}
    finally { setToggling(false); }
  };

  const modeLabel = isHalted ? 'HALTED' : mode.toUpperCase();

  return (
    <>
      {/* Mobile backdrop */}
      {mobileOpen && (
        <div
          className="fixed inset-0 z-40 md:hidden"
          style={{ background: 'var(--modal-backdrop)' }}
          onClick={() => setMobileOpen(false)}
        />
      )}

      <aside
        className={`
          h-screen fixed left-0 top-0 z-50 flex flex-col py-5
          transition-all duration-200
          ${collapsed ? 'md:w-[56px]' : 'md:w-[212px]'}
          w-[212px]
          ${mobileOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}
        `}
        style={{
          background:   'var(--sidebar-bg)',
          borderRight:  '1px solid var(--sidebar-border)',
        }}
      >
        {/* Collapse toggle — desktop only */}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="absolute -right-3 top-7 w-6 h-6 rounded-full items-center justify-center text-[10px] transition-colors z-10 hidden md:flex"
          style={{
            background:  'var(--surface)',
            border:      '1px solid var(--border-2)',
            color:       'var(--text-2)',
          }}
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          {collapsed ? '›' : '‹'}
        </button>

        {/* Close button — mobile only */}
        <button
          onClick={() => setMobileOpen(false)}
          className="absolute right-3 top-4 w-8 h-8 rounded-md flex items-center justify-center md:hidden"
          style={{ color: 'var(--text-2)', background: 'var(--surface-2)' }}
          aria-label="Close menu"
        >
          <span className="material-symbols-outlined" style={{ fontSize: '18px' }}>close</span>
        </button>

        {/* Logo */}
        <div className={`${collapsed ? 'px-3 mb-7 mt-0' : 'px-5 mb-7'}`}>
          {collapsed ? (
            <div
              className="w-8 h-8 rounded-md flex items-center justify-center font-data font-semibold text-xs"
              style={{
                background: 'var(--accent-muted)',
                color:      'var(--accent)',
                border:     '1px solid var(--accent-ring)',
              }}
            >
              PL
            </div>
          ) : (
            <div>
              <div
                className="text-[15px] font-bold tracking-tight"
                style={{ fontFamily: 'Syne', color: 'var(--text)' }}
              >
                Precision Ledger
              </div>
              <div
                className="text-[9px] font-bold tracking-[0.18em] mt-0.5 uppercase"
                style={{ color: 'var(--accent)', fontFamily: 'Syne' }}
              >
                AI Hedge Fund
              </div>
            </div>
          )}
        </div>

        {/* Nav items */}
        <nav className="flex-1 px-2 space-y-0.5 overflow-hidden">
          {NAV_ITEMS.map(({ to, label, icon }) => {
            let dotStyle = null;
            if (label === 'Execution')
              dotStyle = ibkrOk === true ? 'dot-green' : ibkrOk === false ? 'dot-red' : null;
            if (label === 'Risk')
              dotStyle = criticalCount > 0 ? 'dot-red' : null;
            if (label === 'Portfolio')
              dotStyle = pendingCount > 0 ? 'dot-amber' : null;

            return (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                title={collapsed ? label : undefined}
                className={({ isActive }) => `
                  relative flex items-center gap-2.5 px-2.5 py-2 rounded-md text-[11px] font-bold
                  tracking-[0.06em] uppercase transition-all duration-150
                  ${isActive ? '' : 'hover:opacity-80'}
                `}
                style={({ isActive }) => ({
                  color:       isActive ? 'var(--accent)'  : 'var(--text-2)',
                  background:  isActive ? 'var(--accent-muted)' : 'transparent',
                  borderLeft:  isActive ? '2px solid var(--accent)' : '2px solid transparent',
                  paddingLeft: isActive ? '8px' : '10px',
                })}
              >
                <span
                  className="material-symbols-outlined flex-shrink-0"
                  style={{ fontSize: '18px' }}
                  aria-hidden
                >
                  {icon}
                </span>
                {!collapsed && (
                  <>
                    <span className="flex-1" style={{ fontFamily: 'Syne', letterSpacing: '0.07em' }}>
                      {label}
                    </span>
                    {dotStyle && (
                      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${dotStyle}`} />
                    )}
                    {label === 'Portfolio' && pendingCount > 0 && (
                      <span
                        className="text-[9px] font-bold px-1.5 py-0.5 rounded-sm font-data"
                        style={{ background: 'var(--amber-bg)', color: 'var(--amber)' }}
                      >
                        {pendingCount}
                      </span>
                    )}
                  </>
                )}
                {collapsed && dotStyle && (
                  <span className={`absolute right-1.5 top-1.5 w-1.5 h-1.5 rounded-full ${dotStyle}`} />
                )}
              </NavLink>
            );
          })}
        </nav>

        {/* Bottom section */}
        <div className={`${collapsed ? 'px-2' : 'px-3'} mt-auto space-y-2`}>
          {/* Divider */}
          <div style={{ height: '1px', background: 'var(--border)' }} />

          {/* Mode badge */}
          {!collapsed && (
            <button
              onClick={() => !toggling && setShowConfirm(true)}
              disabled={toggling}
              className="w-full rounded-md px-3 py-2 text-[10px] font-bold tracking-[0.12em] uppercase text-center transition-all"
              style={
                isHalted
                  ? { background: 'var(--amber-bg)', color: 'var(--amber)', border: '1px solid var(--amber-border)' }
                  : isAutonomous
                  ? { background: 'var(--accent-muted)', color: 'var(--accent)', border: '1px solid var(--accent-ring)' }
                  : { background: 'transparent', color: 'var(--text-2)', border: '1px solid var(--border)' }
              }
            >
              {modeLabel}
            </button>
          )}

          {criticalCount > 0 && !collapsed && (
            <p className="text-[9px] font-bold text-center" style={{ color: 'var(--red)' }}>
              {criticalCount} CRITICAL alert{criticalCount > 1 ? 's' : ''}
            </p>
          )}

          {/* Controls row: alerts + settings + theme toggle */}
          <div className={`flex ${collapsed ? 'flex-col items-center gap-2' : 'items-center gap-1'}`}>
            {/* Alert bell */}
            <button
              onClick={() => { setNotifOpen(v => !v); if (!notifOpen) loadAlerts(); }}
              className="relative flex items-center gap-1.5 p-1.5 rounded-md transition-all flex-1"
              style={{ color: 'var(--text-2)' }}
              onMouseEnter={e => e.currentTarget.style.color = 'var(--accent)'}
              onMouseLeave={e => e.currentTarget.style.color = 'var(--text-2)'}
              title="Recent alerts"
            >
              <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>notifications</span>
              {recentAlerts.filter(a => !a.resolved).length > 0 && (
                <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full dot-red" />
              )}
              {!collapsed && (
                <span className="text-[10px] font-bold tracking-wide uppercase" style={{ fontFamily: 'Syne' }}>
                  Alerts
                </span>
              )}
            </button>

            {/* Theme toggle button */}
            <button
              onClick={toggleTheme}
              className="relative p-1.5 rounded-md transition-all flex-shrink-0"
              style={{ color: 'var(--text-2)' }}
              onMouseEnter={e => e.currentTarget.style.color = 'var(--accent)'}
              onMouseLeave={e => e.currentTarget.style.color = 'var(--text-2)'}
              title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
            >
              <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>
                {isDark ? 'light_mode' : 'dark_mode'}
              </span>
            </button>

            {/* Settings */}
            {!collapsed && (
              <a
                href="#"
                className="flex items-center p-1.5 rounded-md transition-all flex-shrink-0"
                style={{ color: 'var(--text-2)' }}
                onMouseEnter={e => e.currentTarget.style.color = 'var(--text)'}
                onMouseLeave={e => e.currentTarget.style.color = 'var(--text-2)'}
              >
                <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>settings</span>
              </a>
            )}
          </div>
        </div>
      </aside>

      {/* Notification slide-out panel */}
      {notifOpen && (
        <div
          className={`fixed top-0 ${collapsed ? 'left-[56px]' : 'left-[212px]'} h-screen w-72 z-40 flex flex-col animate-slide-down`}
          style={{
            background:   'var(--surface)',
            borderRight:  '1px solid var(--border)',
            boxShadow:    '8px 0 32px rgba(0,0,0,0.25)',
          }}
        >
          <div
            className="px-4 py-4 flex items-center justify-between"
            style={{ borderBottom: '1px solid var(--border)' }}
          >
            <span
              className="text-sm font-bold"
              style={{ color: 'var(--text)', fontFamily: 'Syne' }}
            >
              Recent Alerts
            </span>
            <button
              onClick={() => setNotifOpen(false)}
              className="text-lg transition-colors"
              style={{ color: 'var(--text-2)' }}
              onMouseEnter={e => e.currentTarget.style.color = 'var(--text)'}
              onMouseLeave={e => e.currentTarget.style.color = 'var(--text-2)'}
            >
              ×
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-2 term-scroll">
            {recentAlerts.length === 0 ? (
              <p className="text-sm text-center py-8" style={{ color: 'var(--text-2)' }}>No alerts</p>
            ) : (
              recentAlerts.map(a => <RiskAlert key={a.id} alert={a} compact />)
            )}
          </div>
        </div>
      )}

      {showConfirm && (
        <ConfirmDialog
          title={isHalted ? 'Resume PM Agent?' : 'Halt new entries?'}
          message={
            isHalted
              ? 'The AI PM will resume opening new positions and running its full decision cycle.'
              : 'The PM will stop opening new positions. Existing positions continue to be monitored.'
          }
          confirmLabel={isHalted ? 'Resume' : 'Halt New Entries'}
          destructive={!isHalted}
          onConfirm={handleConfirmToggle}
          onCancel={() => setShowConfirm(false)}
        />
      )}
    </>
  );
}
