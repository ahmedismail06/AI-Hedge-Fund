import client from './client';
import { cached } from './cache';


// Price bars are expensive Polygon calls; 2min TTL is fine for chart display
const TTL = 120_000;

export const getTickerBars = (ticker, range = '1M') =>
  cached(`market/bars/${ticker}/${range}`, TTL, () =>
    client.get(`/market/bars/${encodeURIComponent(ticker)}`, { params: { range } }).then(r => r.data));
