import axios from 'axios';
import { cached, invalidate } from './cache';

const BASE = import.meta.env.VITE_API_BASE_URL;

const TTL = {
  orders: 15_000,   // Execution page polls every 10s — keep TTL just above that
  fills:  30_000,
  status: 15_000,
};

export const getOrders         = () => cached('execution/orders', TTL.orders, () => axios.get(`${BASE}/execution/orders`).then(r => r.data));
export const getFills          = () => cached('execution/fills',  TTL.fills,  () => axios.get(`${BASE}/execution/fills`).then(r => r.data));
export const getExecutionStatus = () => cached('execution/status', TTL.status, () => axios.get(`${BASE}/execution/status`).then(r => r.data));

export const cancelOrder = (id) =>
  axios.post(`${BASE}/execution/cancel/${id}`).then(r => { invalidate('execution/'); return r.data; });

export const runExecutionCycle = () =>
  axios.post(`${BASE}/execution/cycle/run`).then(r => { invalidate('execution/'); return r.data; });
