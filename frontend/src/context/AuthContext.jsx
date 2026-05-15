import { createContext, useContext, useState, useEffect } from 'react';
import { supabase } from '../lib/supabase';

const AuthContext = createContext(null);
const GUEST_KEY = 'pl_guest_mode';

export function AuthProvider({ children }) {
  const [role, setRole]       = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Resolve initial session
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session) {
        setRole('admin');
      } else if (sessionStorage.getItem(GUEST_KEY) === '1') {
        setRole('guest');
      }
      setLoading(false);
    });

    // Keep role in sync with Supabase session lifecycle
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event, session) => {
      if (event === 'SIGNED_IN') {
        sessionStorage.removeItem(GUEST_KEY);
        setRole('admin');
      } else if (event === 'SIGNED_OUT') {
        setRole(prev => (prev === 'admin' ? null : prev));
      }
    });

    return () => subscription.unsubscribe();
  }, []);

  const login = async (email, password) => {
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) throw error;
  };

  const loginAsGuest = () => {
    sessionStorage.setItem(GUEST_KEY, '1');
    setRole('guest');
  };

  const logout = async () => {
    if (role === 'admin') await supabase.auth.signOut();
    sessionStorage.removeItem(GUEST_KEY);
    setRole(null);
  };

  return (
    <AuthContext.Provider value={{ role, isAdmin: role === 'admin', loading, login, loginAsGuest, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
