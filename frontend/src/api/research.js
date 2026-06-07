import client from './client';
import { cached, invalidate } from './cache';


// Research runs nightly at 5 PM ET — long TTLs appropriate
const TTL = {
  memo:       300_000,
  history:    300_000,
  watchlist:  300_000,
};

export async function triggerResearch(ticker) {
  const { data } = await client.post(`/research/${encodeURIComponent(ticker)}`);
  invalidate(`research/memo/${ticker}`);
  return data;
}

export const getLatestMemo = (ticker) =>
  cached(`research/memo/${ticker}`, TTL.memo, () => client.get(`/research/${encodeURIComponent(ticker)}/latest`).then(r => r.data));

export const getHistory   = () => cached('research/history',   TTL.history,   () => client.get(`/research/history`).then(r => r.data));
export const getWatchlist = () => cached('research/watchlist', TTL.watchlist, () => client.get(`/research/watchlist`).then(r => r.data));

export async function updateMemoStatus(memoId, status) {
  const { data } = await client.post(`/research/${memoId}/status`, { status });
  invalidate('research/');
  return data;
}
