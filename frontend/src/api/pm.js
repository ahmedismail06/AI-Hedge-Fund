import axios from 'axios';
import { cached, invalidate } from './cache';

const BASE = import.meta.env.VITE_API_BASE_URL;

const TTL = {
  status:      45_000,   // PM cycles every 5 min — no need to hit every 30s
  decisions:   45_000,
  calibration: 300_000,
  config:      10_000,    // short TTL so screener_is_running stays fresh
};

export const getPMStatus    = () => cached('pm/status',      TTL.status,      () => axios.get(`${BASE}/pm/status`).then(r => r.data));
export const getPMDecisions = (params = {}) => cached(`pm/decisions/${JSON.stringify(params)}`, TTL.decisions, () => axios.get(`${BASE}/pm/decisions`, { params }).then(r => r.data));
export const getPMDecision  = (id) => cached(`pm/decisions/${id}`, TTL.decisions, () => axios.get(`${BASE}/pm/decisions/${id}`).then(r => r.data));
export const getCalibration = () => cached('pm/calibration', TTL.calibration, () => axios.get(`${BASE}/pm/calibration`).then(r => r.data));
export const getPMConfig    = () => cached('pm/config',      TTL.config,      () => axios.get(`${BASE}/pm/config`).then(r => r.data));

export const overrideDecision = (id, payload) =>
  axios.post(`${BASE}/pm/override/${id}`, payload).then(r => { invalidate('pm/'); return r.data; });

export const forceClose = (ticker) =>
  axios.post(`${BASE}/pm/override/close/${ticker}`).then(r => { invalidate('pm/'); return r.data; });

export const haltPM = () =>
  axios.post(`${BASE}/pm/override/halt`).then(r => { invalidate('pm/status'); return r.data; });

export const resumePM = () =>
  axios.post(`${BASE}/pm/override/resume`).then(r => { invalidate('pm/status'); return r.data; });

export const updatePMConfig = (data) =>
  axios.post(`${BASE}/pm/config`, data).then(r => { invalidate('pm/config'); return r.data; });

export const runPMCycle = (portfolioValue = null) =>
  axios.post(`${BASE}/pm/cycle/run`, null, {
    params: portfolioValue != null ? { portfolio_value: portfolioValue } : {},
  }).then(r => { invalidate('pm/'); return r.data; });
