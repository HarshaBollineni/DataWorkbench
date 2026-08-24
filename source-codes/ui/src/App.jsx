import { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';

import { AuthProvider, useAuth } from './context/AuthContext';
import { WizardProvider } from './context/WizardContext';
import { TourProvider } from './context/TourContext';
import Sidebar from './components/Sidebar';
import ProductTour from './components/ProductTour';
const Login = lazy(() => import('./pages/Login'));
const Inventory = lazy(() => import('./pages/Inventory'));
const DataSourcing = lazy(() => import('./pages/DataSourcing'));
const DQFramework = lazy(() => import('./pages/DQFramework'));
const TestLab = lazy(() => import('./pages/TestLab'));
const AnalyticsArtifactRepository = lazy(() => import('./pages/AnalyticsArtifactRepository'));
const IssueManagement = lazy(() => import('./pages/IssueManagement'));
const IssueRca = lazy(() => import('./pages/IssueRca'));
const KnowledgeBase = lazy(() => import('./pages/KnowledgeBase'));
const Admin = lazy(() => import('./pages/Admin'));
const Profile = lazy(() => import('./pages/Profile'));
const AssetCatalogue = lazy(() => import('./pages/AssetCatalogue'));

function RouteFallback() {
  return <div className="flex min-h-64 items-center justify-center text-sm text-slate-500">Loading workspace…</div>;
}

function Shell() {
  return (
    <div className="flex h-screen w-full font-sans text-slate-800 bg-slate-50">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-y-auto">
        <Suspense fallback={<RouteFallback />}><Routes>
          <Route path="/" element={<Inventory />} />
          <Route path="/data-sourcing" element={<DataSourcing />} />
          <Route path="/asset-catalogue" element={<AssetCatalogue />} />
          {/* Legacy use-case wizard retired — Data Sourcing is the entry point. */}
          <Route path="/new-assessment" element={<Navigate to="/data-sourcing" replace />} />
          <Route path="/test-lab" element={<TestLab />} />
          <Route path="/test-lab/artifacts" element={<AnalyticsArtifactRepository />} />
          <Route path="/issues" element={<IssueManagement />} />
          <Route path="/issues/:issueRowId" element={<IssueRca />} />
          <Route path="/knowledge-base" element={<KnowledgeBase />} />
          <Route path="/dq-framework" element={<DQFramework />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes></Suspense>
      </div>
    </div>
  );
}

function Gate() {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-dq-dark text-slate-300">
        Loading…
      </div>
    );
  }
  if (!user) {
    if (location.pathname === '/login') return <Suspense fallback={<RouteFallback />}><Login /></Suspense>;
    return <Navigate to="/login" replace />;
  }
  if (location.pathname === '/login') return <Navigate to="/" replace />;
  return (
    <TourProvider>
      <WizardProvider>
        <Shell />
        <ProductTour />
      </WizardProvider>
    </TourProvider>
  );
}

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Gate />
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
