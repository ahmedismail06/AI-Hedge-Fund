import axios from 'axios';

const BASE = 'http://localhost:8000';

export const getPMStatus = () =>
  axios.get(`${BASE}/pm/status`).then(r => r.data);

export const getPMDecisions = (params = {}) =>
  axios.get(`${BASE}/pm/decisions`, { params }).then(r => r.data);

export const getPMDecision = (id) =>
  axios.get(`${BASE}/pm/decisions/${id}`).then(r => r.data);

export const overrideDecision = (id, payload) =>
  axios.post(`${BASE}/pm/override/${id}`, payload).then(r => r.data);

export const forceClose = (ticker) =>
  axios.post(`${BASE}/pm/override/close/${ticker}`).then(r => r.data);

export const haltPM = () =>
  axios.post(`${BASE}/pm/override/halt`).then(r => r.data);

export const resumePM = () =>
  axios.post(`${BASE}/pm/override/resume`).then(r => r.data);

export const getCalibration = () =>
  axios.get(`${BASE}/pm/calibration`).then(r => r.data);

export const runPMCycle = (portfolioValue = 25000) =>
  axios.post(`${BASE}/pm/cycle/run`, null, { params: { portfolio_value: portfolioValue } }).then(r => r.data);

export const getPMConfig = () =>
  axios.get(`${BASE}/pm/config`).then(r => r.data);

export const updatePMConfig = (data) =>
  axios.post(`${BASE}/pm/config`, data).then(r => r.data);
