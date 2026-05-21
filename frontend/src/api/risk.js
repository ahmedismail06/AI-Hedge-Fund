import axios from 'axios';
import { cached, invalidate } from './cache';

const BASE = import.meta.env.VITE_API_BASE_URL;

const TTL = {
  critical:   20_000,    // time-sensitive
  alerts:     30_000,
  metrics:   120_000,
  history:   300_000,
};

export const getAlerts         = () => cached('risk/alerts',          TTL.alerts,   () => axios.get(`${BASE}/risk/alerts`).then(r => r.data));
export const getCriticalAlerts = () => cached('risk/alerts/critical', TTL.critical, () => axios.get(`${BASE}/risk/alerts/critical`).then(r => r.data));
export const getMetrics        = () => cached('risk/metrics',         TTL.metrics,  () => axios.get(`${BASE}/risk/metrics`).then(r => r.data));
export const getMetricsHistory = () => cached('risk/metrics/history', TTL.history,  () => axios.get(`${BASE}/risk/metrics/history`).then(r => r.data));

export const resolveAlert = (id) =>
  axios.post(`${BASE}/risk/alerts/${id}/resolve`).then(r => { invalidate('risk/alerts'); return r.data; });

export const runRiskMonitor = () =>
  axios.post(`${BASE}/risk/monitor/run`).then(r => { invalidate('risk/'); return r.data; });

export const runNightlyMetrics = () =>
  axios.post(`${BASE}/risk/metrics/run`).then(r => { invalidate('risk/metrics'); return r.data; });
