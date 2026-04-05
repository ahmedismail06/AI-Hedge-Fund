import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import Portfolio from './pages/Portfolio'
import Execution from './pages/Execution'
import Research from './pages/Research'
import Screener from './pages/Screener'
import Macro from './pages/Macro'
import Risk from './pages/Risk'
import Orchestrator from './pages/Orchestrator'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Layout />}>
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
  )
}
