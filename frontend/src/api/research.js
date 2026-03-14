import axios from 'axios';

const BASE = 'http://localhost:8000';

export async function triggerResearch(ticker) {
  const { data } = await axios.post(`${BASE}/research/${encodeURIComponent(ticker)}`);
  return data;
}

export async function getLatestMemo(ticker) {
  const { data } = await axios.get(`${BASE}/research/${encodeURIComponent(ticker)}/latest`);
  return data;
}

export async function getHistory() {
  const { data } = await axios.get(`${BASE}/research/history`);
  return data;
}

export async function getWatchlist() {
  const { data } = await axios.get(`${BASE}/research/watchlist`);
  return data;
}

export async function updateMemoStatus(memoId, status) {
  const { data } = await axios.post(`${BASE}/research/${memoId}/status`, { status });
  return data;
}
