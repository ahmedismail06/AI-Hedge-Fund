import axios from 'axios';

const BASE = 'http://localhost:8000';

export const getPositions = () => axios.get(`${BASE}/portfolio/positions`).then(r => r.data);
export const getPending = () => axios.get(`${BASE}/portfolio/pending`).then(r => r.data);
export const getExposure = () => axios.get(`${BASE}/portfolio/exposure`).then(r => r.data);
export const getHistory = () => axios.get(`${BASE}/portfolio/history`).then(r => r.data);
export const approveTrade = (id) => axios.post(`${BASE}/portfolio/approve/${id}`).then(r => r.data);
export const rejectTrade = (id) => axios.post(`${BASE}/portfolio/reject/${id}`).then(r => r.data);
