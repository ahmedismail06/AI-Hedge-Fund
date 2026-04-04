import axios from 'axios'

const BASE = 'http://localhost:8000'

export const getPositions = () =>
  axios.get(`${BASE}/portfolio/positions`)

export const getPending = () =>
  axios.get(`${BASE}/portfolio/pending`)

export const getExposure = () =>
  axios.get(`${BASE}/portfolio/exposure`)

export const approvePosition = (id) =>
  axios.post(`${BASE}/portfolio/approve/${id}`)

export const rejectPosition = (id) =>
  axios.post(`${BASE}/portfolio/reject/${id}`)

export const getHistory = () =>
  axios.get(`${BASE}/portfolio/history`)

export const sizePosition = (memoId, portfolioValue = 25000) =>
  axios.post(`${BASE}/portfolio/size`, { memo_id: memoId, portfolio_value: portfolioValue })

// Backwards compatibility
export const getPositionHistory = getHistory
