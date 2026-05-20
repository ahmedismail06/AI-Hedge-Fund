import axios from 'axios';

const BASE = import.meta.env.VITE_API_BASE_URL;

export const getTickerBars = (ticker, range = '1M') =>
  axios.get(`${BASE}/market/bars/${encodeURIComponent(ticker)}`, { params: { range } }).then(r => r.data);
