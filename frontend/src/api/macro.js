import axios from 'axios'

const BASE = 'http://localhost:8000'

export const getRegime = () =>
  axios.get(`${BASE}/macro/regime`)

export const getBriefing = () =>
  axios.get(`${BASE}/macro/briefing`)

export const getIndicators = () =>
  axios.get(`${BASE}/macro/indicators`)

export const getMacroHistory = () =>
  axios.get(`${BASE}/macro/history`)

export const runMacro = () =>
  axios.post(`${BASE}/macro/run`)
