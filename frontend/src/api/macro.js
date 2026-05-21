import axios from 'axios';
import { cached, invalidate } from './cache';

const BASE = import.meta.env.VITE_API_BASE_URL;

// Macro agent runs once daily at 7 AM ET — long TTLs are appropriate
const TTL = {
  briefing:   600_000,
  regime:     600_000,
  history:    600_000,
  indicators: 600_000,
};

export const getBriefing   = () => cached('macro/briefing',   TTL.briefing,   () => axios.get(`${BASE}/macro/briefing`).then(r => r.data));
export const getRegime     = () => cached('macro/regime',     TTL.regime,     () => axios.get(`${BASE}/macro/regime`).then(r => r.data));
export const getMacroHistory = () => cached('macro/history',  TTL.history,    () => axios.get(`${BASE}/macro/history`).then(r => r.data));
export const getIndicators = () => cached('macro/indicators', TTL.indicators, () => axios.get(`${BASE}/macro/indicators`).then(r => r.data));

export const runMacroAgent = () =>
  axios.post(`${BASE}/macro/run`).then(r => { invalidate('macro/'); return r.data; });
