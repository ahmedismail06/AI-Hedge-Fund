import axios from 'axios';

const BASE = import.meta.env.VITE_API_BASE_URL;

export const getBriefing = () => axios.get(`${BASE}/macro/briefing`).then(r => r.data);
export const getRegime = () => axios.get(`${BASE}/macro/regime`).then(r => r.data);
export const getMacroHistory = () => axios.get(`${BASE}/macro/history`).then(r => r.data);
export const getIndicators = () => axios.get(`${BASE}/macro/indicators`).then(r => r.data);
export const runMacroAgent = () => axios.post(`${BASE}/macro/run`).then(r => r.data);
