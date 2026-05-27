import { NavLink } from 'react-router-dom';

const NAV_ITEMS = [
  { to: '/',          label: 'Dash',      icon: 'dashboard',          end: true },
  { to: '/book',      label: 'Book',      icon: 'account_balance_wallet' },
  { to: '/signals',   label: 'Signals',   icon: 'filter_list' },
  { to: '/risk',      label: 'Risk',      icon: 'security' },
  { to: '/macro',     label: 'Macro',     icon: 'language' },
  { to: '/execution', label: 'Exec',      icon: 'bolt' },
];

export default function BottomNav() {
  return (
    <nav className="bottom-nav md:hidden animate-slide-up">
      {NAV_ITEMS.map(({ to, label, icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) => `bottom-nav-item${isActive ? ' active' : ''}`}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '20px' }}>{icon}</span>
          {label}
        </NavLink>
      ))}
    </nav>
  );
}
