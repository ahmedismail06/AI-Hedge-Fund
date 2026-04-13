import axios from 'axios';

const BASE = import.meta.env.VITE_API_BASE_URL;

export const getOrders = () => axios.get(`${BASE}/execution/orders`).then(r => r.data);
export const getFills = () => axios.get(`${BASE}/execution/fills`).then(r => r.data);
export const getExecutionStatus = () => axios.get(`${BASE}/execution/status`).then(r => r.data);
export const cancelOrder = (id) => axios.post(`${BASE}/execution/cancel/${id}`).then(r => r.data);
export const runExecutionCycle = () => axios.post(`${BASE}/execution/cycle/run`).then(r => r.data);
