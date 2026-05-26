import logging
import os
import threading

import httpx
from supabase import Client, ClientOptions, create_client

logger = logging.getLogger(__name__)

_SUPABASE_CLIENT: Client | None = None
_SUPABASE_CLIENT_CREDS: tuple[str, str] | None = None
_SUPABASE_CLIENT_LOCK = threading.Lock()


def _close_supabase_client(client: Client | None) -> None:
    """Best-effort cleanup for a replaced cached client."""
    if client is None:
        return

    http_client = getattr(getattr(client, "options", None), "httpx_client", None)
    if http_client is not None:
        try:
            http_client.close()
        except Exception:
            logger.debug("_close_supabase_client: httpx close failed", exc_info=True)


def _build_supabase_client(url: str, key: str) -> Client:
    """
    Build one shared Supabase client with a preconfigured sync httpx transport.

    We pin Supabase to HTTP/1.1 and warm the lazy PostgREST client under a lock
    so background threads never race through first-time SSL/httpx setup.
    """
    http_client = httpx.Client(
        follow_redirects=True,
        http2=False,
        trust_env=False,
        timeout=httpx.Timeout(120.0),
    )
    options = ClientOptions(
        auto_refresh_token=False,
        persist_session=False,
        httpx_client=http_client,
    )
    client = create_client(url, key, options=options)
    _ = client.postgrest
    return client

def get_supabase_client() -> Client:
    """
    Standardized Supabase client creation.
    Prioritizes SUPABASE_SERVICE_KEY (service_role) to bypass RLS.
    Falls back to SUPABASE_KEY if service key is missing.
    """
    global _SUPABASE_CLIENT, _SUPABASE_CLIENT_CREDS

    url = os.getenv("SUPABASE_URL")
    # Priority: SERVICE_KEY then KEY
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY/SUPABASE_KEY must be set in .env")

    creds = (url, key)
    cached = _SUPABASE_CLIENT
    if cached is not None and _SUPABASE_CLIENT_CREDS == creds:
        return cached

    with _SUPABASE_CLIENT_LOCK:
        if _SUPABASE_CLIENT is not None and _SUPABASE_CLIENT_CREDS == creds:
            return _SUPABASE_CLIENT

        old_client = _SUPABASE_CLIENT
        _SUPABASE_CLIENT = _build_supabase_client(url, key)
        _SUPABASE_CLIENT_CREDS = creds

    _close_supabase_client(old_client)
    return _SUPABASE_CLIENT


def warm_supabase_client() -> Client:
    """Eagerly initialize the shared client on the main thread during startup."""
    return get_supabase_client()
