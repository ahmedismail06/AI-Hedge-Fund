import { createContext, useContext, useState } from 'react';

const SidebarContext = createContext({
  collapsed: false, setCollapsed: () => {},
  mobileOpen: false, setMobileOpen: () => {},
});

export function SidebarProvider({ children }) {
  const [collapsed, setCollapsedState] = useState(() => {
    try { return localStorage.getItem('sidebar_collapsed') === 'true'; } catch { return false; }
  });
  const [mobileOpen, setMobileOpen] = useState(false);

  const setCollapsed = (val) => {
    setCollapsedState(val);
    try { localStorage.setItem('sidebar_collapsed', String(val)); } catch {}
  };

  return (
    <SidebarContext.Provider value={{ collapsed, setCollapsed, mobileOpen, setMobileOpen }}>
      {children}
    </SidebarContext.Provider>
  );
}

export const useSidebar = () => useContext(SidebarContext);
