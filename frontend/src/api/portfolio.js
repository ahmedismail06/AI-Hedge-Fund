import client from './client';
import { cached, invalidate } from './cache';


const TTL = {
  pending:     20_000,   // trades waiting approval — needs freshness
  positions:   45_000,
  exposure:    45_000,
  history:    300_000,
  equity:     300_000,
};

export const getPositions   = () => cached('portfolio/positions',    TTL.positions, () => client.get(`/portfolio/positions`).then(r => r.data));
export const getPending     = () => cached('portfolio/pending',      TTL.pending,   () => client.get(`/portfolio/pending`).then(r => r.data));
export const getExposure    = () => cached('portfolio/exposure',     TTL.exposure,  () => client.get(`/portfolio/exposure`).then(r => r.data));
export const getHistory     = () => cached('portfolio/history',      TTL.history,   () => client.get(`/portfolio/history`).then(r => r.data));
export const getEquityCurve = (days = 30) => cached(`portfolio/equity-curve/${days}`, TTL.equity, () => client.get(`/portfolio/equity-curve`, { params: { days } }).then(r => r.data));

export const approveTrade = (id) =>
  client.post(`/portfolio/approve/${id}`).then(r => { invalidate('portfolio/'); return r.data; });

export const rejectTrade = (id) =>
  client.post(`/portfolio/reject/${id}`).then(r => { invalidate('portfolio/'); return r.data; });
