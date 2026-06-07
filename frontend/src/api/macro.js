import client from './client';
import { cached, invalidate } from './cache';


// Macro agent runs once daily at 7 AM ET — long TTLs are appropriate
const TTL = {
  briefing:   600_000,
  regime:     600_000,
  history:    600_000,
  indicators: 600_000,
};

export const getBriefing   = () => cached('macro/briefing',   TTL.briefing,   () => client.get(`/macro/briefing`).then(r => r.data));
export const getRegime     = () => cached('macro/regime',     TTL.regime,     () => client.get(`/macro/regime`).then(r => r.data));
export const getMacroHistory = () => cached('macro/history',  TTL.history,    () => client.get(`/macro/history`).then(r => r.data));
export const getIndicators = () => cached('macro/indicators', TTL.indicators, () => client.get(`/macro/indicators`).then(r => r.data));

export const runMacroAgent = () =>
  client.post(`/macro/run`).then(r => { invalidate('macro/'); return r.data; });
