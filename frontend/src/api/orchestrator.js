import client from './client';
import { cached, invalidate } from './cache';


const TTL = {
  status: 30_000,
  mode:   60_000,
  log:    120_000,
};

export const getOrchestratorStatus = () =>
  cached('orchestrator/status', TTL.status, () => client.get(`/orchestrator/status`).then(r => r.data));

export const getMode = () =>
  cached('orchestrator/mode', TTL.mode, () => client.get(`/orchestrator/mode`).then(r => r.data));

export const getLog = (runDate, limit = 100) =>
  cached(`orchestrator/log/${runDate}/${limit}`, TTL.log, () =>
    client.get(`/orchestrator/log`, { params: { run_date: runDate, limit } }).then(r => r.data));

export const setMode = (mode) =>
  client.post(`/orchestrator/mode`, { mode }).then(r => { invalidate('orchestrator/mode'); return r; });

export const runCycle = (portfolioValue = null) =>
  client.post(`/orchestrator/cycle/run`, null, {
    params: portfolioValue != null ? { portfolio_value: portfolioValue } : {},
  }).then(r => { invalidate('orchestrator/'); return r; });
