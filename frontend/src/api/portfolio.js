import axios from 'axios';
import { cached, invalidate } from './cache';

const BASE = import.meta.env.VITE_API_BASE_URL;

const TTL = {
  pending:     20_000,   // trades waiting approval — needs freshness
  positions:   45_000,
  exposure:    45_000,
  history:    300_000,
  equity:     300_000,
};

export const getPositions   = () => cached('portfolio/positions',    TTL.positions, () => axios.get(`${BASE}/portfolio/positions`).then(r => r.data));
export const getPending     = () => cached('portfolio/pending',      TTL.pending,   () => axios.get(`${BASE}/portfolio/pending`).then(r => r.data));
export const getExposure    = () => cached('portfolio/exposure',     TTL.exposure,  () => axios.get(`${BASE}/portfolio/exposure`).then(r => r.data));
export const getHistory     = () => cached('portfolio/history',      TTL.history,   () => axios.get(`${BASE}/portfolio/history`).then(r => r.data));
export const getEquityCurve = (days = 30) => cached(`portfolio/equity-curve/${days}`, TTL.equity, () => axios.get(`${BASE}/portfolio/equity-curve`, { params: { days } }).then(r => r.data));

export const approveTrade = (id) =>
  axios.post(`${BASE}/portfolio/approve/${id}`).then(r => { invalidate('portfolio/'); return r.data; });

export const rejectTrade = (id) =>
  axios.post(`${BASE}/portfolio/reject/${id}`).then(r => { invalidate('portfolio/'); return r.data; });
