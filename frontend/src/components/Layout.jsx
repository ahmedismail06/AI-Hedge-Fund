import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import Sidebar from './Sidebar'
import { getRegime } from '../api/macro'

const REGIME_STYLES = {
  'Risk-On':       { bar: 'bg-green-500',  text: 'text-green-700',  bg: 'bg-green-50  border-green-200' },
  'Risk-Off':      { bar: 'bg-red-500',    text: 'text-red-700',    bg: 'bg-red-50    border-red-200' },
  'Stagflation':   { bar: 'bg-yellow-500', text: 'text-yellow-700', bg: 'bg-yellow-50 border-yellow-200' },
  'Transitional':  { bar: 'bg-blue-500',   text: 'text-blue-700',   bg: 'bg-blue-50   border-blue-200' },
}

export default function Layout() {
  const [regime, setRegime] = useState(null)

  useEffect(() => {
    const load = () => getRegime().then(r => setRegime(r.data)).catch(() => {})
    load()
    const id = setInterval(load, 60_000)
    return () => clearInterval(id)
  }, [])

  const styles = REGIME_STYLES[regime?.regime] ?? { bar: 'bg-gray-400', text: 'text-gray-600', bg: 'bg-gray-50 border-gray-200' }

  return (
    <div className="min-h-screen bg-surface text-on-surface selection:bg-primary-fixed selection:text-on-primary-fixed">
      <Sidebar />
      <div className="ml-[220px] flex flex-col min-h-screen">
        {/* Macro regime strip */}
        {regime && (
          <div className={`flex items-center gap-3 px-6 py-1.5 border-b text-[11px] font-bold ${styles.bg}`}>
            <span className={`w-2 h-2 rounded-full ${styles.bar}`}></span>
            <span className={styles.text}>
              {regime.regime}
            </span>
            <span className="text-gray-400 font-normal">·</span>
            <span className="text-gray-500 font-normal">
              Confidence {regime.regime_confidence != null ? Number(regime.regime_confidence).toFixed(1) : '—'}/10
            </span>
            <span className="text-gray-400 font-normal">·</span>
            <span className="text-gray-500 font-normal">
              Score {regime.regime_score != null ? Number(regime.regime_score).toFixed(1) : '—'}
            </span>
          </div>
        )}
        <main className="flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
