import client from './client';
import { cached, invalidate } from './cache';


const TTL = {
  status:      45_000,   // PM cycles every 5 min — no need to hit every 30s
  decisions:   45_000,
  calibration: 300_000,
  config:      10_000,    // short TTL so screener_is_running stays fresh
};

export const getPMStatus    = () => cached('pm/status',      TTL.status,      () => client.get(`/pm/status`).then(r => r.data));
export const getPMDecisions = (params = {}) => cached(`pm/decisions/${JSON.stringify(params)}`, TTL.decisions, () => client.get(`/pm/decisions`, { params }).then(r => r.data));
export const getPMDecision  = (id) => cached(`pm/decisions/${id}`, TTL.decisions, () => client.get(`/pm/decisions/${id}`).then(r => r.data));
export const getCalibration = () => cached('pm/calibration', TTL.calibration, () => client.get(`/pm/calibration`).then(r => r.data));
export const getPMConfig    = () => cached('pm/config',      TTL.config,      () => client.get(`/pm/config`).then(r => r.data));

export const overrideDecision = (id, payload) =>
  client.post(`/pm/override/${id}`, payload).then(r => { invalidate('pm/'); return r.data; });

export const forceClose = (ticker) =>
  client.post(`/pm/override/close/${ticker}`).then(r => { invalidate('pm/'); return r.data; });

export const haltPM = () =>
  client.post(`/pm/override/halt`).then(r => { invalidate('pm/status'); return r.data; });

export const resumePM = () =>
  client.post(`/pm/override/resume`).then(r => { invalidate('pm/status'); return r.data; });

export const updatePMConfig = (data) =>
  client.post(`/pm/config`, data).then(r => { invalidate('pm/config'); return r.data; });

export const runPMCycle = (portfolioValue = null) =>
  client.post(`/pm/cycle/run`, null, {
    params: portfolioValue != null ? { portfolio_value: portfolioValue } : {},
  }).then(r => { invalidate('pm/'); return r.data; });
