import client from './client';
import { cached, invalidate } from './cache';


const TTL = {
  orders: 15_000,   // Execution page polls every 10s — keep TTL just above that
  fills:  30_000,
  status: 15_000,
};

export const getOrders         = () => cached('execution/orders', TTL.orders, () => client.get(`/execution/orders`).then(r => r.data));
export const getFills          = () => cached('execution/fills',  TTL.fills,  () => client.get(`/execution/fills`).then(r => r.data));
export const getExecutionStatus = () => cached('execution/status', TTL.status, () => client.get(`/execution/status`).then(r => r.data));

export const cancelOrder = (id) =>
  client.post(`/execution/cancel/${id}`).then(r => { invalidate('execution/'); return r.data; });

export const runExecutionCycle = () =>
  client.post(`/execution/cycle/run`).then(r => { invalidate('execution/'); return r.data; });
