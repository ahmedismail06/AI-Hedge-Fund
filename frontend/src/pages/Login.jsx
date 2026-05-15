import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';

export default function Login() {
  const { login, loginAsGuest } = useAuth();
  const navigate = useNavigate();
  const [email,    setEmail]    = useState('');
  const [password, setPassword] = useState('');
  const [error,    setError]    = useState('');
  const [loading,  setLoading]  = useState(false);

  const handleAdminLogin = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      await login(email.trim(), password);
      navigate('/', { replace: true });
    } catch (err) {
      setError(err.message ?? 'Invalid credentials');
      setPassword('');
    } finally {
      setLoading(false);
    }
  };

  const handleGuest = () => {
    loginAsGuest();
    navigate('/', { replace: true });
  };

  return (
    <div
      className="min-h-screen flex items-center justify-center p-4"
      style={{ background: 'var(--bg)' }}
    >
      <div
        className="w-full max-w-sm rounded-2xl p-8"
        style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
      >
        {/* Logo */}
        <div className="mb-8 text-center">
          <div
            className="text-[22px] font-black tracking-tight"
            style={{ fontFamily: 'Syne', color: 'var(--text)' }}
          >
            Precision Ledger
          </div>
          <div
            className="text-[10px] font-bold tracking-[0.2em] uppercase mt-1"
            style={{ color: 'var(--accent)', fontFamily: 'Syne' }}
          >
            AI Hedge Fund
          </div>
        </div>

        {/* Admin login */}
        <div className="mb-6">
          <div
            className="text-[9px] font-bold tracking-[0.18em] uppercase mb-3"
            style={{ color: 'var(--text-3)', fontFamily: 'Syne' }}
          >
            Admin Access
          </div>
          <form onSubmit={handleAdminLogin} className="space-y-3">
            <input
              type="email"
              value={email}
              onChange={e => { setEmail(e.target.value); setError(''); }}
              placeholder="Email"
              autoFocus
              autoComplete="email"
              style={{
                width: '100%',
                background: 'var(--input-bg)',
                border: `1px solid ${error ? 'var(--red-border)' : 'var(--border)'}`,
                color: 'var(--text)',
                fontFamily: 'JetBrains Mono',
                fontSize: '14px',
                padding: '10px 14px',
                borderRadius: '10px',
                outline: 'none',
                boxSizing: 'border-box',
              }}
              onFocus={e => { e.target.style.borderColor = 'var(--accent-ring)'; }}
              onBlur={e => { e.target.style.borderColor = error ? 'var(--red-border)' : 'var(--border)'; }}
            />
            <input
              type="password"
              value={password}
              onChange={e => { setPassword(e.target.value); setError(''); }}
              placeholder="Password"
              autoComplete="current-password"
              style={{
                width: '100%',
                background: 'var(--input-bg)',
                border: `1px solid ${error ? 'var(--red-border)' : 'var(--border)'}`,
                color: 'var(--text)',
                fontFamily: 'JetBrains Mono',
                fontSize: '14px',
                padding: '10px 14px',
                borderRadius: '10px',
                outline: 'none',
                boxSizing: 'border-box',
              }}
              onFocus={e => { e.target.style.borderColor = 'var(--accent-ring)'; }}
              onBlur={e => { e.target.style.borderColor = error ? 'var(--red-border)' : 'var(--border)'; }}
            />
            {error && (
              <p className="text-[12px]" style={{ color: 'var(--red)', fontFamily: 'Syne' }}>
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={loading || !email.trim() || !password}
              className="w-full py-2.5 rounded-xl font-bold text-[13px] transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
              style={{
                background: 'var(--accent)',
                color: '#000',
                fontFamily: 'Syne',
                border: 'none',
                cursor: loading || !email.trim() || !password ? 'not-allowed' : 'pointer',
              }}
            >
              {loading ? 'Logging in…' : 'Login'}
            </button>
          </form>
        </div>

        {/* Divider */}
        <div className="flex items-center gap-3 mb-6">
          <div className="flex-1 h-px" style={{ background: 'var(--border)' }} />
          <span className="text-[11px]" style={{ color: 'var(--text-3)', fontFamily: 'Syne' }}>or</span>
          <div className="flex-1 h-px" style={{ background: 'var(--border)' }} />
        </div>

        {/* Guest access */}
        <div>
          <div
            className="text-[9px] font-bold tracking-[0.18em] uppercase mb-3"
            style={{ color: 'var(--text-3)', fontFamily: 'Syne' }}
          >
            Guest Access
          </div>
          <button
            onClick={handleGuest}
            className="w-full py-2.5 rounded-xl font-bold text-[13px] transition-all"
            style={{
              background: 'var(--surface-2)',
              color: 'var(--text-2)',
              fontFamily: 'Syne',
              border: '1px solid var(--border)',
              cursor: 'pointer',
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent-ring)'; e.currentTarget.style.color = 'var(--text)'; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-2)'; }}
          >
            Continue as Guest →
          </button>
          <p className="text-[11px] mt-2 text-center" style={{ color: 'var(--text-3)' }}>
            Read-only — no pipeline access
          </p>
        </div>
      </div>
    </div>
  );
}
