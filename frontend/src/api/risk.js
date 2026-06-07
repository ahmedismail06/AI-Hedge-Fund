import client from './client';
import { cached, invalidate } from './cache';


const TTL = {
  critical:   20_000,    // time-sensitive
  alerts:     30_000,
  metrics:   120_000,
  history:   300_000,
};

export const getAlerts         = () => cached('risk/alerts',          TTL.alerts,   () => client.get(`/risk/alerts`).then(r => r.data));
export const getCriticalAlerts = () => cached('risk/alerts/critical', TTL.critical, () => client.get(`/risk/alerts/critical`).then(r => r.data));
export const getMetrics        = () => cached('risk/metrics',         TTL.metrics,  () => client.get(`/risk/metrics`).then(r => r.data));
export const getMetricsHistory = () => cached('risk/metrics/history', TTL.history,  () => client.get(`/risk/metrics/history`).then(r => r.data));

export const resolveAlert = (id) =>
  client.post(`/risk/alerts/${id}/resolve`).then(r => { invalidate('risk/alerts'); return r.data; });

export const runRiskMonitor = () =>
  client.post(`/risk/monitor/run`).then(r => { invalidate('risk/'); return r.data; });

export const runNightlyMetrics = () =>
  client.post(`/risk/metrics/run`).then(r => { invalidate('risk/metrics'); return r.data; });
