import axios from 'axios';
import { cached, invalidate } from './cache';

const BASE = import.meta.env.VITE_API_BASE_URL;

// Research runs nightly at 5 PM ET — long TTLs appropriate
const TTL = {
  memo:       300_000,
  history:    300_000,
  watchlist:  300_000,
};

export async function triggerResearch(ticker) {
  const { data } = await axios.post(`${BASE}/research/${encodeURIComponent(ticker)}`);
  invalidate(`research/memo/${ticker}`);
  return data;
}

export const getLatestMemo = (ticker) =>
  cached(`research/memo/${ticker}`, TTL.memo, () => axios.get(`${BASE}/research/${encodeURIComponent(ticker)}/latest`).then(r => r.data));

export const getHistory   = () => cached('research/history',   TTL.history,   () => axios.get(`${BASE}/research/history`).then(r => r.data));
export const getWatchlist = () => cached('research/watchlist', TTL.watchlist, () => axios.get(`${BASE}/research/watchlist`).then(r => r.data));

export async function updateMemoStatus(memoId, status) {
  const { data } = await axios.post(`${BASE}/research/${memoId}/status`, { status });
  invalidate('research/');
  return data;
}
