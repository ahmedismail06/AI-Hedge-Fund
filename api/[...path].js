const BACKEND = (process.env.BACKEND_URL || 'http://34.59.81.82:8000').trim().replace(/\/$/, '');

module.exports = async function handler(req, res) {
  const segments = Array.isArray(req.query.path)
    ? req.query.path
    : req.query.path
    ? [req.query.path]
    : [];

  const qs = req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : '';
  const url = `${BACKEND}/${segments.join('/')}${qs}`;

  const forwardHeaders = { ...req.headers };
  delete forwardHeaders['host'];        // must not forward — causes GCP routing failure
  delete forwardHeaders['connection'];  // hop-by-hop, must not forward per HTTP spec

  try {
    const options = {
      method: req.method,
      headers: forwardHeaders,
    };

    if (!['GET', 'HEAD'].includes(req.method) && req.body) {
      options.body = typeof req.body === 'string'
        ? req.body
        : JSON.stringify(req.body);
    }

    const upstream = await fetch(url, options);
    const text = await upstream.text();

    res.status(upstream.status);
    upstream.headers.forEach((value, key) => {
      if (!['transfer-encoding', 'connection', 'keep-alive'].includes(key.toLowerCase())) {
        res.setHeader(key, value);
      }
    });
    res.send(text);
  } catch (err) {
    console.error('[proxy] upstream error:', err.message, 'url:', url);
    res.status(502).json({ error: 'Bad gateway', detail: err.message });
  }
};
