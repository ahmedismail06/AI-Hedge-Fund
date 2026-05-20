import { useState } from 'react';
import { Outlet } from 'react-router-dom';
import TopNav from './TopNav';
import PnlRibbon from './PnlRibbon';

export default function Layout() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="min-h-screen" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
      <TopNav mobileOpen={mobileOpen} setMobileOpen={setMobileOpen} />
      <PnlRibbon />
      <main style={{ paddingTop: '88px', overflowX: 'hidden', minWidth: 0 }}>
        <Outlet />
      </main>
    </div>
  );
}
