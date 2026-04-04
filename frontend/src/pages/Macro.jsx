import { useEffect, useState } from 'react'
import { getBriefing, getIndicators, getRegime, runMacro } from '../api/macro'

const SCORE_CONFIG = [
  { key: 'growth_score', label: 'Growth Momentum', icon: 'trending_up', color: 'text-primary', bar: 'bg-primary', valuePrefix: '+' },
  { key: 'inflation_score', label: 'Inflation Trend', icon: 'trending_down', color: 'text-tertiary', bar: 'bg-tertiary', valuePrefix: '' },
  { key: 'fed_score', label: 'Fed Policy Pivot', icon: 'trending_down', color: 'text-on-surface-variant', bar: 'bg-outline-variant', valuePrefix: '' },
  { key: 'stress_score', label: 'Systemic Stress', icon: 'horizontal_rule', color: 'text-on-surface-variant', bar: 'bg-on-surface-variant/20', valuePrefix: '' },
]

export default function Macro() {
  const [briefing, setBriefing] = useState(null)
  const [indicators, setIndicators] = useState([])
  const [regime, setRegime] = useState(null)
  const [running, setRunning] = useState(false)

  const load = () => {
    getBriefing()
      .then(r => {
        const data = r?.data ?? r
        const safe = data && typeof data === 'object' && !Array.isArray(data) ? data : null
        setBriefing(safe)
      })
      .catch(() => {})
    getIndicators()
      .then(r => {
        const list = Array.isArray(r?.data?.indicators) ? r.data.indicators : []
        setIndicators(list)
      })
      .catch(() => {})
    getRegime()
      .then(r => {
        const data = r?.data ?? r
        const safe = data && typeof data === 'object' && !Array.isArray(data) ? data : null
        setRegime(safe)
      })
      .catch(() => {})
  }

  useEffect(() => {
    load()
  }, [])

  const handleRun = async () => {
    setRunning(true)
    try {
      await runMacro()
      load()
    } catch {
      // silent
    } finally {
      setRunning(false)
    }
  }

  const bannerRegime = briefing?.regime || regime?.regime || '—'
  const bannerScore = briefing?.regime_score ?? regime?.regime_score
  const bannerConf = briefing?.regime_confidence ?? regime?.regime_confidence
  const bannerConfDisplay = Number.isFinite(Number(bannerConf)) ? Number(bannerConf).toFixed(1) : '—'
  const updatedLabel = briefing?.date || regime?.date || 'Updated recently'
  const portfolioGuidance = briefing?.portfolio_guidance
  const qualitativeSummary = briefing?.qualitative_summary
  const themes = Array.isArray(briefing?.key_themes) ? briefing.key_themes : []

  return (
    <div>
      <header className="w-full flex justify-between items-center px-8 py-4 bg-transparent">
        <div className="flex items-center flex-1 max-w-xl">
          <div className="relative w-full">
            <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant text-lg">search</span>
            <input className="w-full pl-10 pr-4 py-2 bg-surface-container-low border-none rounded-xl text-sm focus:ring-0 placeholder:text-on-surface-variant/50" placeholder="Analyze global macro trends..." type="text" />
          </div>
        </div>
        <div className="flex items-center space-x-6">
          <button className="text-on-surface-variant hover:bg-slate-100 p-2 rounded-lg transition-all scale-100 active:scale-95">
            <span className="material-symbols-outlined">notifications</span>
          </button>
          <button className="text-on-surface-variant hover:bg-slate-100 p-2 rounded-lg transition-all">
            <span className="material-symbols-outlined">history</span>
          </button>
          <div className="flex items-center ml-4 space-x-3">
            <div className="text-right">
              <p className="text-xs font-bold text-on-surface">DR. ELARA VANCE</p>
              <p className="text-[10px] text-on-surface-variant tracking-tighter">CHIEF STRATEGIST</p>
            </div>
            <span className="material-symbols-outlined text-3xl text-primary" style={{ fontVariationSettings: "'FILL' 1" }}>account_circle</span>
          </div>
        </div>
      </header>

      <div className="px-8 pb-12">
        <section className="mt-4 mb-8">
          <div className="bg-surface-container-lowest rounded-xl p-8 relative overflow-hidden group">
            <div className="absolute top-0 right-0 w-1/3 h-full opacity-10 pointer-events-none transition-transform group-hover:scale-110">
              <img
                className="w-full h-full object-cover"
                alt="abstract macro backdrop"
                src="https://lh3.googleusercontent.com/aida-public/AB6AXuCMElcdnXygS8amVjhKdmpsz6v_ehGD2bsYvGqFrGVAgY8M7FF0D8OJV7hMJnlgVOT5kyUgsxqxsakihEM0kndK5mviyb4ZVVrnkdS8uD83B-tj8EnRI7do0dyOqZviRDVs7HtJ7loKOwNRHAQeWA2KX4k_Ado8YIhp6NW7Pg7uEsc78dzFLA7Mv4Bd6ZoqfSbfyAxkghHzoSamuXt5MWMj1sAZmENSwuQ6hccl2oJh4T-llTu_O9_OsVyVgeR02I8Yimvh7dg3dPk"
              />
            </div>
            <div className="relative z-10 flex flex-col md:flex-row md:items-end justify-between gap-6">
              <div>
                <div className="flex items-center gap-3 mb-4">
                  <span className="bg-primary text-on-primary px-3 py-1 text-[11px] font-bold tracking-widest rounded-sm uppercase">Global Regime</span>
                  <span className="text-on-surface-variant text-sm flex items-center gap-1">
                    <span className="material-symbols-outlined text-xs">schedule</span>
                    {updatedLabel}
                  </span>
                </div>
                <h2 className="text-5xl font-bold text-on-surface mb-2">{bannerRegime}</h2>
                <p className="text-on-surface-variant max-w-lg leading-relaxed">
                  {briefing?.regime_summary || 'Current market conditions indicate robust liquidity and positive real growth signals.'}
                </p>
              </div>
              <div className="flex items-center gap-12 bg-surface-container-low px-8 py-6 rounded-xl">
                <div className="text-center">
                  <p className="label-sm text-on-surface-variant mb-1 uppercase tracking-widest text-[10px] font-bold">Regime Score</p>
                  <p className="text-4xl font-extrabold text-primary">{bannerScore ?? '—'}</p>
                </div>
                <div className="h-10 w-[1px] bg-outline-variant/30"></div>
                <div className="text-center">
                  <p className="label-sm text-on-surface-variant mb-1 uppercase tracking-widest text-[10px] font-bold">AI Confidence</p>
                  <p className="text-4xl font-extrabold text-on-surface">{bannerConfDisplay}<span className="text-xl text-on-surface-variant">/10</span></p>
                </div>
                <button
                  onClick={handleRun}
                  disabled={running}
                  className="signature-gradient text-on-primary px-6 py-3 rounded-xl font-bold text-sm flex items-center gap-2 hover:opacity-90 active:scale-95 transition-all shadow-lg shadow-primary/20 disabled:opacity-60"
                >
                  <span className="material-symbols-outlined text-lg">smart_toy</span>
                  {running ? 'Running…' : 'Run Macro Agent'}
                </button>
              </div>
            </div>
          </div>
        </section>

        <section className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-12">
          {SCORE_CONFIG.map(card => {
            const value = Number(briefing?.[card.key] ?? 0)
            const pct = Math.min(Math.abs(value) * 100, 100)
            return (
              <div key={card.key} className="bg-surface-container-lowest p-6 rounded-xl hover:bg-surface-container-high transition-colors cursor-default">
                <p className="label-sm text-on-surface-variant mb-4 uppercase tracking-wider text-[10px] font-bold">{card.label}</p>
                <div className="flex items-baseline justify-between">
                  <span className="text-2xl font-bold">{Number.isFinite(value) ? `${card.valuePrefix}${value.toFixed(2)}` : '—'}</span>
                  <span className={`${card.color} flex items-center text-sm font-bold`}>
                    <span className="material-symbols-outlined text-lg">{card.icon}</span>
                  </span>
                </div>
                <div className="mt-4 w-full h-1 bg-surface-container-low rounded-full overflow-hidden">
                  <div className={`${card.bar} h-full`} style={{ width: `${pct}%` }}></div>
                </div>
              </div>
            )
          })}
        </section>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
          <div className="lg:col-span-7 space-y-8">
            <div className="bg-surface-container-lowest p-8 rounded-xl relative overflow-hidden border-l-4 border-primary">
              <h3 className="text-xl font-bold mb-6 flex items-center gap-2">
                <span className="material-symbols-outlined text-primary">account_tree</span>
                Portfolio Guidance
              </h3>
              <div className="space-y-4 text-on-surface leading-relaxed text-sm">
                <p>{portfolioGuidance || 'Based on the current regime, we recommend a balanced tilt toward quality growth with selective defensive hedges.'}</p>
                {Array.isArray(briefing?.sector_tilts) && briefing.sector_tilts.length > 0 && (
                  <div className="pt-4 flex gap-4 flex-wrap">
                    {briefing.sector_tilts.slice(0, 3).map((tilt, idx) => (
                      <div key={idx} className="bg-primary-container text-on-primary-container px-4 py-2 rounded-lg text-xs font-semibold">
                        {typeof tilt === 'string' ? tilt : `${tilt.sector || 'Sector'} ${tilt.tilt || ''}`.trim()}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <div className="bg-surface-container-lowest p-8 rounded-xl border-l-4 border-outline-variant">
              <h3 className="text-xl font-bold mb-6 flex items-center gap-2">
                <span className="material-symbols-outlined text-on-surface-variant">description</span>
                Qualitative Summary
              </h3>
              <div className="text-on-surface-variant text-sm leading-relaxed space-y-4 italic">
                <p>{qualitativeSummary || 'No qualitative summary available. Run the Macro Agent to generate a fresh briefing.'}</p>
              </div>
            </div>
          </div>
          <div className="lg:col-span-5">
            <div className="bg-surface-container-lowest p-8 rounded-xl">
              <h3 className="text-xl font-bold mb-8 flex items-center gap-2">
                <span className="material-symbols-outlined text-tertiary">lightbulb</span>
                Key Themes
              </h3>
              <ul className="space-y-6">
                {(themes.length ? themes : ['Disinflationary Growth', 'EM Resilience', 'Energy Volatility Floor']).map((theme, idx) => (
                  <li key={idx} className="flex items-start gap-4">
                    <div className="w-10 h-10 rounded-full bg-primary-fixed flex items-center justify-center shrink-0">
                      <span className="material-symbols-outlined text-primary text-xl">electric_bolt</span>
                    </div>
                    <div>
                      <h4 className="font-bold text-sm">{typeof theme === 'string' ? theme : theme.title}</h4>
                      <p className="text-xs text-on-surface-variant mt-1 leading-normal">{typeof theme === 'string' ? 'Theme summary pending.' : theme.detail}</p>
                    </div>
                  </li>
                ))}
              </ul>
              <div className="mt-10 p-4 bg-surface-container-low rounded-lg">
                <div className="flex items-center gap-3 mb-2">
                  <span className="material-symbols-outlined text-sm text-tertiary">warning</span>
                  <span className="text-[10px] font-bold uppercase tracking-wider text-tertiary">Tail Risk Alert</span>
                </div>
                <p className="text-[11px] text-on-surface-variant">
                  {briefing?.tail_risk || 'Yield curve dynamics remain inverted, suggesting a probability of volatility expansion within the next two quarters.'}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
