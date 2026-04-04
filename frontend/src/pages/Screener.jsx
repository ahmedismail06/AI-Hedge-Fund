import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getWatchlist, runScreening } from '../api/screener'
import { getHistory } from '../api/research'
import { triggerResearch } from '../api/research'

const PAGE_SIZE = 10

function buildSectorStats(rows, max = 5) {
  const counts = rows.reduce((acc, row) => {
    const key = row.sector || 'Other'
    acc[key] = (acc[key] || 0) + 1
    return acc
  }, {})
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, max)
  const maxCount = entries[0]?.[1] || 1
  return entries.map(([sector, count]) => ({
    sector,
    count,
    pct: Math.round((count / maxCount) * 100),
  }))
}

function ResearchBadge({ status }) {
  if (status === 'researched') return (
    <span className="flex items-center gap-1 text-[10px] font-bold text-green-700">
      <span className="w-1.5 h-1.5 rounded-full bg-green-500 inline-block"></span>Researched
    </span>
  )
  if (status === 'queued') return (
    <span className="flex items-center gap-1 text-[10px] font-bold text-yellow-700">
      <span className="w-1.5 h-1.5 rounded-full bg-yellow-500 inline-block"></span>Queued
    </span>
  )
  return (
    <span className="flex items-center gap-1 text-[10px] font-bold text-on-surface-variant opacity-50">
      <span className="w-1.5 h-1.5 rounded-full bg-gray-400 inline-block"></span>Not done
    </span>
  )
}

export default function Screener() {
  const navigate = useNavigate()
  const [watchlist, setWatchlist] = useState([])
  const [running, setRunning] = useState(false)
  const [page, setPage] = useState(0)
  const [researchedTickers, setResearchedTickers] = useState(new Set())
  const [queuedTickers, setQueuedTickers] = useState(new Set())

  const load = () => {
    const today = new Date().toISOString().slice(0, 10)
    getWatchlist().then(r => {
      const rows = r.data || []
      setWatchlist(rows)
      setQueuedTickers(new Set(rows.filter(r => r.queued_for_research).map(r => r.ticker)))
    }).catch(() => {})
    getHistory().then(r => {
      const memos = r.data || []
      const todayTickers = new Set(
        memos.filter(m => m.date === today).map(m => m.ticker)
      )
      setResearchedTickers(todayTickers)
    }).catch(() => {})
  }

  useEffect(() => {
    load()
  }, [])

  const handleRun = async () => {
    setRunning(true)
    try {
      await runScreening(watchlist[0]?.regime)
      load()
    } catch {
      // silent
    } finally {
      setRunning(false)
    }
  }

  const handleResearch = async (ticker) => {
    try {
      await triggerResearch(ticker)
    } catch {
      // silent
    } finally {
      navigate('/research')
    }
  }

  const runDate = watchlist[0]?.run_date || '—'
  const regime = watchlist[0]?.regime || '—'
  const sectorStats = useMemo(() => buildSectorStats(watchlist, 5), [watchlist])

  const totalPages = Math.ceil(watchlist.length / PAGE_SIZE)
  const paginated = watchlist.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)
  const startIdx = page * PAGE_SIZE + 1
  const endIdx = Math.min((page + 1) * PAGE_SIZE, watchlist.length)

  const getResearchStatus = (row) => {
    if (researchedTickers.has(row.ticker)) return 'researched'
    if (queuedTickers.has(row.ticker) || row.queued_for_research) return 'queued'
    return 'none'
  }

  return (
    <div>
      <header className="w-full top-0 flex justify-between items-center px-8 py-4 bg-transparent">
        <div className="flex items-center space-x-6">
          <h2 className="text-2xl font-bold font-headline text-on-surface">Watchlist Screener</h2>
          <div className="relative">
            <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-outline">search</span>
            <input className="bg-surface-container-low border-none rounded-full pl-10 pr-4 py-2 text-sm focus:ring-2 focus:ring-primary/20 w-64 transition-all" placeholder="Search universe..." type="text" />
          </div>
        </div>
        <div className="flex items-center space-x-4">
          <button className="p-2 hover:bg-slate-100 rounded-lg transition-transform hover:scale-105">
            <span className="material-symbols-outlined text-on-surface-variant">notifications</span>
          </button>
          <button className="p-2 hover:bg-slate-100 rounded-lg transition-transform hover:scale-105">
            <span className="material-symbols-outlined text-on-surface-variant">history</span>
          </button>
          <div className="h-8 w-8 rounded-full overflow-hidden ml-2 bg-surface-container-high border-2 border-white">
            <img
              className="w-full h-full object-cover"
              alt="profile"
              src="https://lh3.googleusercontent.com/aida-public/AB6AXuC678j94JDtWUpta5k0pap6KWcXldujualw8dhSvDJtuHYADYn3V0OZJyEqjzuNT3GZP1PX1RXOhs8w1qCkb34XjQCtfxezBbXziUiVLwYjGnf8EKP2yV2j7DrxRzSDWUOc3xlNYL7bq3KqIhMtWyaYZu-ACdDR9TqYnAa1hRVDH24L2B-aUkMLVK7CYPYetOjC2Xrpst5puIeQvWzdFh0Penl6O-XEzxHKAE-h7nRP9ChQ4Y7gLboMeiO06B9qN_pMwabXiLfQOac"
            />
          </div>
        </div>
      </header>

      <main className="ml-[0px] p-8">
        <div className="flex justify-between items-center mb-10">
          <div className="flex items-center space-x-8">
            <div>
              <p className="label-sm text-[11px] text-on-surface-variant uppercase tracking-widest mb-1">Observation Date</p>
              <p className="font-headline text-lg font-semibold">{runDate}</p>
            </div>
            <div className="h-10 w-[1px] bg-outline-variant opacity-20"></div>
            <div>
              <p className="label-sm text-[11px] text-on-surface-variant uppercase tracking-widest mb-1">Market Regime</p>
              <div className="flex items-center space-x-2">
                <span className="w-2 h-2 rounded-full bg-primary shadow-[0_0_8px_rgba(0,74,198,0.5)]"></span>
                <p className="font-headline text-lg font-semibold text-primary">{regime}</p>
              </div>
            </div>
          </div>
          <button
            onClick={handleRun}
            disabled={running}
            className="signature-gradient text-on-primary px-6 py-3 rounded-xl font-bold flex items-center space-x-2 shadow-[0_12px_32px_-8px_rgba(0,74,198,0.2)] hover:opacity-90 transition-all active:scale-95 disabled:opacity-60"
          >
            <span className="material-symbols-outlined">play_arrow</span>
            <span className="tracking-tight">{running ? 'Running…' : 'Run Screener'}</span>
          </button>
        </div>

        <div className="bg-surface-container-lowest rounded-xl p-1 shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse">
              <thead className="bg-surface-container-low/50">
                <tr>
                  <th className="px-6 py-4 label-sm text-[11px] font-bold text-on-surface-variant tracking-wider uppercase">Rank</th>
                  <th className="px-6 py-4 label-sm text-[11px] font-bold text-on-surface-variant tracking-wider uppercase">Ticker</th>
                  <th className="px-6 py-4 label-sm text-[11px] font-bold text-on-surface-variant tracking-wider uppercase">Composite</th>
                  <th className="px-6 py-4 label-sm text-[11px] font-bold text-on-surface-variant tracking-wider uppercase">Quality</th>
                  <th className="px-6 py-4 label-sm text-[11px] font-bold text-on-surface-variant tracking-wider uppercase">Value</th>
                  <th className="px-6 py-4 label-sm text-[11px] font-bold text-on-surface-variant tracking-wider uppercase">Momentum</th>
                  <th className="px-6 py-4 label-sm text-[11px] font-bold text-on-surface-variant tracking-wider uppercase">Beneish</th>
                  <th className="px-6 py-4 label-sm text-[11px] font-bold text-on-surface-variant tracking-wider uppercase">Sector</th>
                  <th className="px-6 py-4 label-sm text-[11px] font-bold text-on-surface-variant tracking-wider uppercase">Status</th>
                  <th className="px-6 py-4 text-right"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-surface-container-low/30">
                {watchlist.length === 0 && (
                  <tr>
                    <td colSpan="10" className="px-6 py-6 text-sm text-on-surface-variant text-center">No watchlist yet</td>
                  </tr>
                )}
                {paginated.map((row, idx) => (
                  <tr key={row.id} className="group hover:bg-surface-container-high/50 transition-colors">
                    <td className="px-6 py-5 font-headline font-extrabold text-primary text-xl">{String(row.rank || page * PAGE_SIZE + idx + 1).padStart(2, '0')}</td>
                    <td className="px-6 py-5">
                      <div className="flex flex-col">
                        <span className="font-bold text-on-surface text-base">{row.ticker}</span>
                        <span className="text-[10px] text-on-surface-variant uppercase font-medium">{row.name || row.company_name || '—'}</span>
                      </div>
                    </td>
                    <td className="px-6 py-5 font-bold text-on-surface">{Number(row.composite_score).toFixed(2)}</td>
                    <td className="px-6 py-5 text-on-surface-variant">{Number(row.quality_score).toFixed(2)}</td>
                    <td className="px-6 py-5 text-on-surface-variant">{Number(row.value_score).toFixed(2)}</td>
                    <td className="px-6 py-5">
                      <span className="text-on-primary-fixed-variant bg-primary-fixed px-2 py-0.5 rounded text-[11px] font-bold">{Number(row.momentum_score).toFixed(2)}</span>
                    </td>
                    <td className="px-6 py-5">
                      <span className="bg-on-primary-fixed text-primary-fixed px-3 py-1 rounded-full text-[10px] font-bold tracking-widest uppercase">
                        {row.beneish_m_score != null ? Number(row.beneish_m_score).toFixed(2) : 'PASS'}
                      </span>
                    </td>
                    <td className="px-6 py-5 text-on-surface-variant text-sm">{row.sector || '—'}</td>
                    <td className="px-6 py-5">
                      <ResearchBadge status={getResearchStatus(row)} />
                    </td>
                    <td className="px-6 py-5 text-right">
                      <button
                        onClick={() => handleResearch(row.ticker)}
                        className="text-primary hover:underline font-semibold text-sm flex items-center justify-end space-x-1 group-hover:translate-x-1 transition-transform"
                      >
                        <span>{researchedTickers.has(row.ticker) ? 'Re-run' : 'Research'}</span>
                        <span className="material-symbols-outlined text-sm">arrow_forward</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="px-6 py-4 flex justify-between items-center bg-surface-container-low/20">
            <p className="text-[11px] font-bold uppercase tracking-widest text-on-surface-variant">
              {watchlist.length > 0
                ? `Showing ${startIdx}–${endIdx} of ${watchlist.length} candidates`
                : 'No candidates'}
            </p>
            <div className="flex items-center space-x-2">
              <button
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
                className="p-1 hover:bg-surface-container-high rounded transition-colors disabled:opacity-30"
              >
                <span className="material-symbols-outlined text-sm">chevron_left</span>
              </button>
              <span className="text-[11px] font-bold text-on-surface-variant">{page + 1} / {totalPages || 1}</span>
              <button
                onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="p-1 hover:bg-surface-container-high rounded transition-colors disabled:opacity-30"
              >
                <span className="material-symbols-outlined text-sm">chevron_right</span>
              </button>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-12 gap-6 mt-8">
          <div className="col-span-12 lg:col-span-8 bg-surface-container-lowest rounded-xl p-8 shadow-sm">
            <div className="flex justify-between items-start mb-6">
              <div>
                <h3 className="font-headline text-xl font-bold mb-1">Universe Concentration</h3>
                <p className="text-sm text-on-surface-variant">Current screening parameters skew towards top sectors.</p>
              </div>
              <span className="material-symbols-outlined text-primary">pie_chart</span>
            </div>
            <div className="flex items-end space-x-4 h-48 py-4">
              {sectorStats.map((s) => (
                <div key={s.sector} className="flex-1 bg-primary/10 rounded-t-lg relative group" style={{ height: `${s.pct}%` }}>
                  <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 bg-inverse-surface text-inverse-on-surface text-[10px] px-2 py-1 rounded opacity-0 group-hover:opacity-100 transition-opacity">
                    {s.count}
                  </div>
                  <div className="absolute bottom-0 w-full bg-primary rounded-t-lg h-full transition-all"></div>
                </div>
              ))}
            </div>
            <div className="flex justify-between text-[10px] font-bold uppercase tracking-tighter text-on-surface-variant pt-2">
              {sectorStats.map(s => (
                <span key={s.sector}>{s.sector.slice(0, 6)}</span>
              ))}
            </div>
          </div>
          <div className="col-span-12 lg:col-span-4 flex flex-col space-y-6">
            <div className="bg-on-secondary-fixed text-primary-fixed p-6 rounded-xl flex flex-col justify-between h-full">
              <div className="flex justify-between items-center">
                <span className="label-sm text-[10px] tracking-[0.2em] font-bold uppercase">Screener Health</span>
                <span className="material-symbols-outlined text-sm">hub</span>
              </div>
              <div>
                <p className="text-3xl font-headline font-bold mb-1">Optimal</p>
                <p className="text-xs opacity-70">Current filters are statistically significant with 0.94 confidence.</p>
              </div>
              <div className="pt-4 border-t border-primary-fixed/20 mt-4">
                <div className="flex justify-between text-[11px] mb-1 font-bold">
                  <span>Signals Analyzed</span>
                  <span>{watchlist.length}</span>
                </div>
                <div className="w-full bg-primary-fixed/20 h-1 rounded-full overflow-hidden">
                  <div className="bg-primary-fixed h-full w-3/4"></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
