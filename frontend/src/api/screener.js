import axios from 'axios'

const BASE = 'http://localhost:8000'

export const getWatchlist = (runDate) =>
  axios.get(`${BASE}/screening/watchlist`, {
    params: runDate ? { run_date: runDate } : {},
  })

export const runScreening = (regime) =>
  axios.post(`${BASE}/screening/run`, null, {
    params: regime ? { regime } : {},
  })
