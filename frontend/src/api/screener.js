import axios from 'axios';
import { cached, invalidate } from './cache';

const BASE = import.meta.env.VITE_API_BASE_URL;

// Screener runs nightly at 4 PM ET
const TTL = 300_000;

export const getWatchlist = (allTime = false) =>
  cached(`screening/watchlist/${allTime}`, TTL, () =>
    axios.get(`${BASE}/screening/watchlist${allTime ? '?all_time=true&limit=50' : ''}`).then(r => r.data));

export const runScreener = () =>
  axios.post(`${BASE}/screening/run`).then(r => { invalidate('screening/'); return r.data; });
