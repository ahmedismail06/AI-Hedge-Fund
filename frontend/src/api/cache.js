const store = new Map();

export function cached(key, ttlMs, fetcher) {
  const entry = store.get(key);
  if (entry && entry.expiresAt > Date.now()) {
    return Promise.resolve(entry.data);
  }
  return fetcher().then(data => {
    store.set(key, { data, expiresAt: Date.now() + ttlMs });
    return data;
  });
}

export function invalidate(prefix) {
  for (const key of store.keys()) {
    if (key.startsWith(prefix)) store.delete(key);
  }
}
