import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { getOrchestratorStatus, setMode } from '../api/orchestrator'
import ConfirmDialog from './ConfirmDialog'

const NAV_ITEMS = [
  { to: '/', label: 'Dashboard', icon: 'dashboard' },
  { to: '/portfolio', label: 'Portfolio', icon: 'account_balance_wallet' },
  { to: '/execution', label: 'Execution', icon: 'bolt' },
  { to: '/research', label: 'Research', icon: 'query_stats' },
  { to: '/screener', label: 'Screener', icon: 'filter_list' },
  { to: '/macro', label: 'Macro', icon: 'language' },
  { to: '/risk', label: 'Risk', icon: 'security' },
  { to: '/orchestrator', label: 'Orchestrator', icon: 'memory' },
]

function modeBadgeClasses(mode, suspended) {
  if (mode === 'AUTONOMOUS' && suspended) return 'bg-tertiary-fixed text-tertiary border-tertiary-fixed/60'
  if (mode === 'AUTONOMOUS') return 'bg-primary-fixed text-on-primary-fixed-variant border-primary-fixed-dim'
  return 'bg-on-primary-fixed text-primary-fixed border-primary-fixed-dim'
}

export default function Sidebar() {
  const [status, setStatus] = useState(null)
  const [showConfirm, setShowConfirm] = useState(false)
  const [toggling, setToggling] = useState(false)

  const load = () => {
    getOrchestratorStatus()
      .then(r => setStatus(r.data))
      .catch(() => {})
  }

  useEffect(() => {
    load()
    const id = setInterval(load, 30_000)
    return () => clearInterval(id)
  }, [])

  const mode = status?.mode ?? 'SUPERVISED'
  const suspended = status?.suspended_today ?? false
  const criticalCount = status?.critical_alert_count ?? 0
  const targetMode = mode === 'SUPERVISED' ? 'AUTONOMOUS' : 'SUPERVISED'

  const handleConfirmToggle = async () => {
    setToggling(true)
    setShowConfirm(false)
    try {
      await setMode(targetMode)
      load()
    } catch {
      // silent
    } finally {
      setToggling(false)
    }
  }

  return (
    <aside className="h-screen w-[220px] fixed left-0 top-0 bg-slate-100 dark:bg-slate-900 flex flex-col py-6 z-50">
      <div className="px-6 mb-8">
        <div className="text-xl font-bold text-slate-900 dark:text-white font-headline">Precision Ledger</div>
        <div className="text-[10px] font-bold tracking-widest text-slate-500 uppercase mt-1">AI Hedge Fund</div>
      </div>

      <nav className="flex-1 px-3 space-y-1">
        {NAV_ITEMS.map(({ to, label, icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 text-[11px] font-bold tracking-wider uppercase transition-colors group ${
                isActive
                  ? 'text-blue-700 dark:text-blue-400 font-semibold border-r-2 border-blue-700 bg-slate-200/40 dark:bg-slate-800/40'
                  : 'text-slate-500 dark:text-slate-400 hover:bg-slate-200 dark:hover:bg-slate-800 rounded-lg'
              }`
            }
          >
            <span className="material-symbols-outlined text-[20px]" aria-hidden>
              {icon}
            </span>
            <span className="flex-1">{label}</span>
            {label === 'Risk' && criticalCount > 0 && (
              <span className="w-2 h-2 rounded-full bg-error" />
            )}
          </NavLink>
        ))}
      </nav>

      <div className="px-6 mt-auto space-y-4">
        <button
          onClick={() => !toggling && setShowConfirm(true)}
          disabled={toggling}
          className={`w-full border rounded-lg px-3 py-2 text-[10px] font-black tracking-widest text-center transition-colors ${modeBadgeClasses(
            mode,
            suspended
          )}`}
        >
          {mode === 'AUTONOMOUS' && suspended ? 'AUTONOMOUS (SUSPENDED)' : mode}
        </button>
        {criticalCount > 0 && (
          <p className="text-[10px] font-semibold text-error text-center">
            {criticalCount} CRITICAL alert{criticalCount > 1 ? 's' : ''}
          </p>
        )}
        <div className="flex flex-col gap-2 pt-4 border-t border-slate-200 dark:border-slate-800">
          <a
            className="flex items-center gap-3 text-slate-500 dark:text-slate-400 hover:text-primary transition-colors"
            href="#"
          >
            <span className="material-symbols-outlined text-sm">settings</span>
            <span className="text-[11px] font-bold tracking-wider uppercase">Settings</span>
          </a>
          <a
            className="flex items-center gap-3 text-slate-500 dark:text-slate-400 hover:text-primary transition-colors"
            href="#"
          >
            <span className="material-symbols-outlined text-sm">help</span>
            <span className="text-[11px] font-bold tracking-wider uppercase">Support</span>
          </a>
        </div>
      </div>

      {showConfirm && (
        <ConfirmDialog
          title={`Switch to ${targetMode}?`}
          message={
            targetMode === 'AUTONOMOUS'
              ? 'Autonomous mode will auto-approve positions with conviction ≥ 8.5. A 5% intraday drawdown will suspend it for the day.'
              : 'Switching to Supervised mode requires human approval for all new positions. The daily suspension will also be cleared.'
          }
          confirmLabel={`Enable ${targetMode}`}
          destructive={targetMode === 'AUTONOMOUS'}
          onConfirm={handleConfirmToggle}
          onCancel={() => setShowConfirm(false)}
        />
      )}
    </aside>
  )
}
