import axios from 'axios';

const BASE = import.meta.env.VITE_API_BASE_URL;

export const getWatchlist = (allTime = false) => axios.get(`${BASE}/screening/watchlist${allTime ? '?all_time=true&limit=50' : ''}`).then(r => r.data);
export const runScreener = () => axios.post(`${BASE}/screening/run`).then(r => r.data);
