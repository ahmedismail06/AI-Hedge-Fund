import axios from 'axios';

const BASE = import.meta.env.VITE_API_BASE_URL;

export const getWatchlist = () => axios.get(`${BASE}/screening/watchlist`).then(r => r.data);
export const runScreener = () => axios.post(`${BASE}/screening/run`).then(r => r.data);
