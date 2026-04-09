import axios from 'axios';

const BASE = 'http://localhost:8000';

export const getWatchlist = () => axios.get(`${BASE}/screening/watchlist`).then(r => r.data);
export const runScreener = () => axios.post(`${BASE}/screening/run`).then(r => r.data);
