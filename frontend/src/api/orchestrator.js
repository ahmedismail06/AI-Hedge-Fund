import axios from 'axios'

const BASE = import.meta.env.VITE_API_BASE_URL

export const getOrchestratorStatus = () =>
  axios.get(`${BASE}/orchestrator/status`)

export const getMode = () =>
  axios.get(`${BASE}/orchestrator/mode`)

export const setMode = (mode) =>
  axios.post(`${BASE}/orchestrator/mode`, { mode })

export const runCycle = (portfolioValue = 25000) =>
  axios.post(`${BASE}/orchestrator/cycle/run`, null, {
    params: { portfolio_value: portfolioValue },
  })

export const getLog = (runDate, limit = 100) =>
  axios.get(`${BASE}/orchestrator/log`, {
    params: { run_date: runDate, limit },
  })
