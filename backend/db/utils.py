import os
from typing import Optional
from supabase import create_client, Client

def get_supabase_client() -> Client:
    """
    Standardized Supabase client creation.
    Prioritizes SUPABASE_SERVICE_KEY (service_role) to bypass RLS.
    Falls back to SUPABASE_KEY if service key is missing.
    """
    url = os.getenv("SUPABASE_URL")
    # Priority: SERVICE_KEY then KEY
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY/SUPABASE_KEY must be set in .env")
        
    return create_client(url, key)
