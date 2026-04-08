import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { getPMStatus, haltPM, resumePM } from '../api/pm';
import { getCriticalAlerts, getAlerts } from '../api/risk';
import { getExecutionStatus } from '../api/execution';
import { getPending } from '../api/portfolio';
import { useSidebar } from '../context/SidebarContext';
import ConfirmDialog from './ConfirmDialog';
import RiskAlert from './RiskAlert';

const NAV_ITEMS = [
  { to: '/',            label: 'Dashboard',    icon: 'dashboard' },
  { to: '/portfolio',   label: 'Portfolio',    icon: 'account_balance_wallet' },
  { to: '/execution',   label: 'Execution',    icon: 'bolt' },
  { to: '/research',    label: 'Research',     icon: 'query_stats' },
  { to: '/screener',    label: 'Screener',     icon: 'filter_list' },
  { to: '/macro',       label: 'Macro',        icon: 'language' },
  { to: '/risk',        label: 'Risk',         icon: 'security' },
  { to: '/orchestrator',label: 'Orchestrator', icon: 'memory' },
];

export default function Sidebar() {
  const { collapsed, setCollapsed } = useSidebar();
  const [status, setStatus] = useState(null);
  const [showConfirm, setShowConfirm] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [notifOpen, setNotifOpen] = useState(false);
  const [recentAlerts, setRecentAlerts] = useState([]);
  const [ibkrOk, setIbkrOk] = useState(null);
  const [pendingCount, setPendingCount] = useState(0);

  const loadStatus = () => {
    getPMStatus().then(r => setStatus(r)).catch(() => {});
  };

  const loadHealth = () => {
    getCriticalAlerts().catch(() => {});
    getExecutionStatus()
      .then(r => { const d = r?.data || r || {}; setIbkrOk(d.connected ?? d.ibkr_connected ?? null); })
      .catch(() => {});
    getPending()
      .then(r => setPendingCount(Array.isArray(r) ? r.length : 0))
      .catch(() => {});
  };

  const loadAlerts = () => {
    getAlerts().then(r => setRecentAlerts(Array.isArray(r) ? r.slice(0, 10) : [])).catch(() => {});
  };

  useEffect(() => {
    loadStatus(); loadHealth(); loadAlerts();
    const id1 = setInterval(loadStatus, 30000);
    const id2 = setInterval(loadHealth, 60000);
    return () => { clearInterval(id1); clearInterval(id2); };
  }, []);

  const mode = status?.mode ?? 'autonomous';
  const isHalted = status?.daily_loss_halt_triggered ?? false;
  const criticalCount = status?.active_critical_alerts ?? 0;
  const isAutonomous = mode === 'autonomous';

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
  const modeColor = isHalted
    ? 'bg-yellow-100 text-yellow-700 border-yellow-300'
    : isAutonomous
    ? 'bg-blue-600 text-white border-blue-600'
    : 'bg-white text-gray-700 border-gray-300';

  return (
    <>
      <aside className={`h-screen ${collapsed ? 'w-[60px]' : 'w-[220px]'} fixed left-0 top-0 bg-slate-100 dark:bg-slate-900 flex flex-col py-6 z-50 transition-all duration-200`}>
        {/* Collapse Toggle */}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="absolute -right-3 top-8 w-6 h-6 rounded-full bg-white border border-gray-300 shadow-sm flex items-center justify-center text-gray-500 hover:bg-gray-50 z-10 text-xs"
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? '›' : '‹'}
        </button>

        {/* Logo */}
        {!collapsed && (
          <div className="px-6 mb-8">
            <div className="text-xl font-bold text-slate-900 dark:text-white font-headline">Precision Ledger</div>
            <div className="text-[10px] font-bold tracking-widest text-slate-500 uppercase mt-1">AI Hedge Fund</div>
          </div>
        )}
        {collapsed && <div className="px-4 mb-8 mt-1 text-slate-700 font-bold text-xs text-center">PL</div>}

        {/* Nav */}
        <nav className="flex-1 px-2 space-y-1 overflow-hidden">
          {NAV_ITEMS.map(({ to, label, icon }) => {
            // Per-item status dot
            let dotColor = null;
            if (label === 'Execution') dotColor = ibkrOk === true ? 'bg-green-400' : ibkrOk === false ? 'bg-red-400' : null;
            if (label === 'Risk') dotColor = criticalCount > 0 ? 'bg-red-500' : null;
            if (label === 'Portfolio') dotColor = pendingCount > 0 ? 'bg-yellow-400' : null;

            return (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                title={collapsed ? label : undefined}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 text-[11px] font-bold tracking-wider uppercase transition-colors group rounded-lg ${
                    isActive
                      ? 'text-blue-700 dark:text-blue-400 bg-slate-200/60 dark:bg-slate-800/60'
                      : 'text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-800'
                  }`
                }
              >
                <span className="material-symbols-outlined text-[20px] flex-shrink-0" aria-hidden>{icon}</span>
                {!collapsed && (
                  <>
                    <span className="flex-1">{label}</span>
                    {dotColor && <span className={`w-2 h-2 rounded-full flex-shrink-0 ${dotColor}`} />}
                    {label === 'Portfolio' && pendingCount > 0 && (
                      <span className="text-[9px] font-bold bg-yellow-100 text-yellow-700 px-1 rounded">{pendingCount}</span>
                    )}
                  </>
                )}
                {collapsed && dotColor && (
                  <span className={`absolute right-1 top-1 w-1.5 h-1.5 rounded-full ${dotColor}`} />
                )}
              </NavLink>
            );
          })}
        </nav>

        {/* Bottom Controls */}
        <div className={`${collapsed ? 'px-2' : 'px-6'} mt-auto space-y-3`}>
          {/* Mode badge */}
          {!collapsed && (
            <button
              onClick={() => !toggling && setShowConfirm(true)}
              disabled={toggling}
              className={`w-full border rounded-lg px-3 py-2 text-[10px] font-black tracking-widest text-center transition-colors ${modeColor}`}
            >
              {modeLabel}
            </button>
          )}

          {criticalCount > 0 && !collapsed && (
            <p className="text-[10px] font-semibold text-red-600 text-center">
              {criticalCount} CRITICAL alert{criticalCount > 1 ? 's' : ''}
            </p>
          )}

          {/* Notification bell */}
          <div className={`flex ${collapsed ? 'flex-col items-center gap-2' : 'gap-3 pt-3 border-t border-slate-200 dark:border-slate-800'} `}>
            <button
              onClick={() => { setNotifOpen(v => !v); if (!notifOpen) loadAlerts(); }}
              className="relative flex items-center gap-2 text-slate-500 hover:text-blue-600 transition-colors p-1 rounded-lg hover:bg-slate-200"
              title="Recent alerts"
            >
              <span className="material-symbols-outlined text-[18px]">notifications</span>
              {recentAlerts.filter(a => !a.resolved).length > 0 && (
                <span className="absolute -top-0.5 -right-0.5 w-2 h-2 bg-red-500 rounded-full" />
              )}
              {!collapsed && <span className="text-[11px] font-bold tracking-wider uppercase">Alerts</span>}
            </button>
            {!collapsed && (
              <>
                <a href="#" className="flex items-center gap-2 text-slate-500 hover:text-blue-600 transition-colors p-1">
                  <span className="material-symbols-outlined text-[18px]">settings</span>
                  <span className="text-[11px] font-bold tracking-wider uppercase">Settings</span>
                </a>
              </>
            )}
          </div>
        </div>
      </aside>

      {/* Notification Slide-Out Panel */}
      {notifOpen && (
        <div className={`fixed top-0 ${collapsed ? 'left-[60px]' : 'left-[220px]'} h-screen w-72 bg-white border-r border-gray-200 shadow-xl z-40 flex flex-col transition-all duration-200`}>
          <div className="px-4 py-4 border-b border-gray-100 flex items-center justify-between">
            <span className="text-sm font-semibold text-gray-900">Recent Alerts</span>
            <button onClick={() => setNotifOpen(false)} className="text-gray-400 hover:text-gray-700 text-lg">×</button>
          </div>
          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {recentAlerts.length === 0 ? (
              <p className="text-sm text-gray-400 text-center py-6">No alerts</p>
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
