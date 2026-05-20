import { Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import { ThemeProvider } from './context/ThemeContext';
import Layout from './components/Layout';
import Login from './pages/Login';
import CommandCenter from './pages/CommandCenter';
import Book from './pages/Book';
import Execution from './pages/Execution';
import Signals from './pages/Signals';
import Macro from './pages/Macro';
import RiskEngine from './pages/RiskEngine';

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
            <Route index element={<CommandCenter />} />
            <Route path="book" element={<Book />} />
            <Route path="execution" element={<Execution />} />
            <Route path="signals" element={<Signals />} />
            <Route path="macro" element={<Macro />} />
            <Route path="risk" element={<RiskEngine />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </ThemeProvider>
    </AuthProvider>
  );
}
