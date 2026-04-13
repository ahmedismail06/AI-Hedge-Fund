const BACKEND = process.env.BACKEND_URL;

export default async function handler(req, res) {
  const { path = [] } = req.query;
  const url = `${BACKEND}/${path.join('/')}${req.url.includes('?') ? '?' + req.url.split('?')[1] : ''}`;

  try {
    const upstream = await fetch(url, {
      method: req.method,
      headers: {
        'content-type': req.headers['content-type'] || 'application/json',
      },
      body: ['GET', 'HEAD'].includes(req.method) ? undefined : JSON.stringify(req.body),
    });

    const data = await upstream.text();
    res.status(upstream.status);
    upstream.headers.forEach((value, key) => {
      if (!['transfer-encoding', 'connection'].includes(key)) res.setHeader(key, value);
    });
    res.send(data);
  } catch (err) {
    res.status(502).json({ error: 'Bad gateway', detail: err.message });
  }
}
