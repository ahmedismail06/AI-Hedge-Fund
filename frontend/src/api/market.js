import axios from 'axios';
import { cached } from './cache';

const BASE = import.meta.env.VITE_API_BASE_URL;

// Price bars are expensive Polygon calls; 2min TTL is fine for chart display
const TTL = 120_000;

export const getTickerBars = (ticker, range = '1M') =>
  cached(`market/bars/${ticker}/${range}`, TTL, () =>
    axios.get(`${BASE}/market/bars/${encodeURIComponent(ticker)}`, { params: { range } }).then(r => r.data));
