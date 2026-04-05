import { useEffect, useState } from 'react'
import { getLog, runCycle } from '../api/orchestrator'

const EVENT_BADGE = {
  CYCLE_START:     'bg-gray-100 text-gray-600',
  CYCLE_END:       'bg-gray-100 text-gray-600',
  AUTO_APPROVE:    'bg-green-100 text-green-700',
  CRITICAL_BLOCK:  'bg-red-100 text-red-700',
  SUSPEND:         'bg-yellow-100 text-yellow-700',
  MODE_CHANGE:     'bg-blue-100 text-blue-700',
  AGENT_TRIGGERED: 'bg-purple-100 text-purple-700',
  ERROR:           'bg-red-100 text-red-700',
}

function toLocalDate(isoDate) {
  // YYYY-MM-DD → date object in local time (avoids UTC offset shift)
  const [y, m, d] = isoDate.split('-').map(Number)
  return new Date(y, m - 1, d)
}

function formatDateInput(date) {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

export default function Orchestrator() {
  const today = formatDateInput(new Date())
  const [date, setDate] = useState(today)
  const [log, setLog] = useState([])
  const [running, setRunning] = useState(false)

  const load = (d) => {
    getLog(d).then(r => setLog(r.data || [])).catch(() => {})
  }

  useEffect(() => {
    load(date)
  }, [date])

  const handleRunCycle = async () => {
    setRunning(true)
    try {
      await runCycle()
      load(date)
    } catch {
      // silent
    } finally {
      setRunning(false)
    }
  }

  const cycleCount = log.filter(e => e.event_type === 'CYCLE_END').length
  const autoApproveCount = log.filter(e => e.event_type === 'AUTO_APPROVE').length
  const errorCount = log.filter(e => e.event_type === 'ERROR').length

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-gray-900">Orchestrator</h1>
          <p className="text-xs text-gray-500 mt-1">
            {log.length} events · {cycleCount} cycles · {autoApproveCount} auto-approvals
            {errorCount > 0 && <span className="text-red-500 ml-1">· {errorCount} errors</span>}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="date"
            value={date}
            onChange={e => setDate(e.target.value)}
            className="text-xs border border-gray-300 rounded-lg px-3 py-2 text-gray-700 focus:outline-none focus:ring-2 focus:ring-blue-200"
          />
          <button
            onClick={handleRunCycle}
            disabled={running}
            className="text-xs font-semibold px-3 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {running ? 'Running…' : 'Run Cycle'}
          </button>
        </div>
      </div>

      <div className="bg-white rounded-xl border border-gray-200">
        <div className="px-5 py-4 border-b border-gray-100">
          <p className="text-sm font-semibold text-gray-900">Audit Log</p>
        </div>
        <div className="overflow-auto">
          <table className="min-w-full text-left">
            <thead className="text-xs uppercase tracking-wide text-gray-400 bg-gray-50">
              <tr>
                <th className="px-4 py-3 whitespace-nowrap">Time</th>
                <th className="px-4 py-3">Event</th>
                <th className="px-4 py-3">Agent</th>
                <th className="px-4 py-3">Ticker</th>
                <th className="px-4 py-3 text-right">Conviction</th>
                <th className="px-4 py-3">Mode</th>
                <th className="px-4 py-3">Detail</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {log.length === 0 && (
                <tr>
                  <td colSpan="7" className="px-4 py-6 text-sm text-gray-400 text-center">
                    No events for {date}
                  </td>
                </tr>
              )}
              {log.map(entry => (
                <tr key={entry.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-xs text-gray-400 whitespace-nowrap font-mono">
                    {entry.created_at
                      ? new Date(entry.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                      : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold whitespace-nowrap ${EVENT_BADGE[entry.event_type] || 'bg-gray-100 text-gray-600'}`}>
                      {entry.event_type}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">{entry.agent || '—'}</td>
                  <td className="px-4 py-3 text-xs font-mono text-gray-700">{entry.ticker || '—'}</td>
                  <td className="px-4 py-3 text-xs text-right font-mono text-gray-600">
                    {entry.conviction_score != null ? Number(entry.conviction_score).toFixed(1) : '—'}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">{entry.mode_snapshot || '—'}</td>
                  <td className="px-4 py-3 text-xs text-gray-600 max-w-sm truncate" title={entry.detail}>
                    {entry.detail || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
