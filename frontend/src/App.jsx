import { Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import { SidebarProvider } from './context/SidebarContext';
import { ThemeProvider } from './context/ThemeContext';
import Layout from './components/Layout';
import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Portfolio from './pages/Portfolio';
import Execution from './pages/Execution';
import Research from './pages/Research';
import Screener from './pages/Screener';
import Macro from './pages/Macro';
import Risk from './pages/Risk';
import Orchestrator from './pages/Orchestrator';

function ProtectedRoute({ children }) {
  const { role, loading } = useAuth();
  if (loading) return null;
  if (role === null) return <Navigate to="/login" replace />;
  return children;
}

function LoginRoute() {
  const { role, loading } = useAuth();
  if (loading) return null;
  if (role !== null) return <Navigate to="/" replace />;
  return <Login />;
}

export default function App() {
  return (
    <AuthProvider>
      <ThemeProvider>
        <SidebarProvider>
          <Routes>
            <Route path="/login" element={<LoginRoute />} />
            <Route
              path="/"
              element={
                <ProtectedRoute>
                  <Layout />
                </ProtectedRoute>
              }
            >
              <Route index element={<Dashboard />} />
              <Route path="portfolio" element={<Portfolio />} />
              <Route path="execution" element={<Execution />} />
              <Route path="research" element={<Research />} />
              <Route path="screener" element={<Screener />} />
              <Route path="macro" element={<Macro />} />
              <Route path="risk" element={<Risk />} />
              <Route path="orchestrator" element={<Orchestrator />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Route>
          </Routes>
        </SidebarProvider>
      </ThemeProvider>
    </AuthProvider>
  );
}
