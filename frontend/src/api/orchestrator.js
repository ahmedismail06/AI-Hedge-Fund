import axios from 'axios';
import { cached, invalidate } from './cache';

const BASE = import.meta.env.VITE_API_BASE_URL;

const TTL = {
  status: 30_000,
  mode:   60_000,
  log:    120_000,
};

export const getOrchestratorStatus = () =>
  cached('orchestrator/status', TTL.status, () => axios.get(`${BASE}/orchestrator/status`).then(r => r.data));

export const getMode = () =>
  cached('orchestrator/mode', TTL.mode, () => axios.get(`${BASE}/orchestrator/mode`).then(r => r.data));

export const getLog = (runDate, limit = 100) =>
  cached(`orchestrator/log/${runDate}/${limit}`, TTL.log, () =>
    axios.get(`${BASE}/orchestrator/log`, { params: { run_date: runDate, limit } }).then(r => r.data));

export const setMode = (mode) =>
  axios.post(`${BASE}/orchestrator/mode`, { mode }).then(r => { invalidate('orchestrator/mode'); return r; });

export const runCycle = (portfolioValue = null) =>
  axios.post(`${BASE}/orchestrator/cycle/run`, null, {
    params: portfolioValue != null ? { portfolio_value: portfolioValue } : {},
  }).then(r => { invalidate('orchestrator/'); return r; });
