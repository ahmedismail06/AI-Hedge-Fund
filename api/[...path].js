const BACKEND = process.env.BACKEND_URL;

module.exports = async function handler(req, res) {
  const segments = req.query.path || [];
  const qs = req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : '';
  const url = `${BACKEND}/${segments.join('/')}${qs}`;

  try {
    const options = {
      method: req.method,
      headers: { 'content-type': req.headers['content-type'] || 'application/json' },
    };
    if (!['GET', 'HEAD'].includes(req.method) && req.body) {
      options.body = JSON.stringify(req.body);
    }

    const upstream = await fetch(url, options);
    const text = await upstream.text();

    res.status(upstream.status);
    const ct = upstream.headers.get('content-type');
    if (ct) res.setHeader('content-type', ct);
    res.send(text);
  } catch (err) {
    res.status(502).json({ error: 'Bad gateway', detail: err.message });
  }
};
