import client from './client';
import { cached, invalidate } from './cache';


// Screener runs nightly at 4 PM ET
const TTL = 300_000;

export const getWatchlist = (allTime = false) =>
  cached(`screening/watchlist/${allTime}`, TTL, () =>
    client.get(`/screening/watchlist${allTime ? '?all_time=true&limit=50' : ''}`).then(r => r.data));

export const runScreener = () =>
  client.post(`/screening/run`).then(r => { invalidate('screening/'); return r.data; });
