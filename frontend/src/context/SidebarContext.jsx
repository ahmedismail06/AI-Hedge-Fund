import { createContext, useContext, useState, useEffect } from 'react';

const SidebarContext = createContext({ collapsed: false, setCollapsed: () => {} });

export function SidebarProvider({ children }) {
  const [collapsed, setCollapsedState] = useState(() => {
    try { return localStorage.getItem('sidebar_collapsed') === 'true'; } catch { return false; }
  });

  const setCollapsed = (val) => {
    setCollapsedState(val);
    try { localStorage.setItem('sidebar_collapsed', String(val)); } catch {}
  };

  return (
    <SidebarContext.Provider value={{ collapsed, setCollapsed }}>
      {children}
    </SidebarContext.Provider>
  );
}

export const useSidebar = () => useContext(SidebarContext);
