import axios from 'axios'

const BASE = 'http://localhost:8000'

export const getAlerts = () =>
  axios.get(`${BASE}/risk/alerts`)

export const resolveAlert = (id) =>
  axios.post(`${BASE}/risk/alerts/${id}/resolve`)

export const getMetrics = () =>
  axios.get(`${BASE}/risk/metrics`)

export const runRiskMonitor = () =>
  axios.post(`${BASE}/risk/monitor/run`)

export const runNightlyMetrics = () =>
  axios.post(`${BASE}/risk/metrics/run`)
