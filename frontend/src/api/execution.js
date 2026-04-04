import axios from 'axios'

const BASE = 'http://localhost:8000'

export const getOrders = () =>
  axios.get(`${BASE}/execution/orders`)

export const getOrder = (id) =>
  axios.get(`${BASE}/execution/orders/${id}`)

export const getFills = () =>
  axios.get(`${BASE}/execution/fills`)

export const cancelOrder = (id) =>
  axios.post(`${BASE}/execution/cancel/${id}`)

export const getExecutionStatus = () =>
  axios.get(`${BASE}/execution/status`)

export const runExecutionCycle = () =>
  axios.post(`${BASE}/execution/cycle/run`)
