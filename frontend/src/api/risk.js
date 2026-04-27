import axios from 'axios';

const BASE = import.meta.env.VITE_API_BASE_URL;

export const getAlerts = () => axios.get(`${BASE}/risk/alerts`).then(r => r.data);
export const getCriticalAlerts = () => axios.get(`${BASE}/risk/alerts/critical`).then(r => r.data);
export const resolveAlert = (id) => axios.post(`${BASE}/risk/alerts/${id}/resolve`).then(r => r.data);
export const getMetrics = () => axios.get(`${BASE}/risk/metrics`).then(r => r.data);
export const getMetricsHistory = () => axios.get(`${BASE}/risk/metrics/history`).then(r => r.data);
export const runRiskMonitor = () => axios.post(`${BASE}/risk/monitor/run`).then(r => r.data);
export const runNightlyMetrics = () => axios.post(`${BASE}/risk/metrics/run`).then(r => r.data);
