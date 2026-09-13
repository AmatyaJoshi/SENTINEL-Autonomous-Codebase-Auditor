import { Suspense, lazy, useCallback } from "react";
import { Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AppShell } from "@/components/AppShell";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Toasts } from "@/components/Toasts";
import { Skeleton } from "@/components/ui";
import { useHotkey } from "@/hooks/useHotkey";

const Overview = lazy(() => import("@/pages/Overview"));
const Runs = lazy(() => import("@/pages/Runs"));
const RunDetail = lazy(() => import("@/pages/RunDetail"));
const Bench = lazy(() => import("@/pages/Bench"));
const Settings = lazy(() => import("@/pages/Settings"));
const NotFound = lazy(() => import("@/pages/NotFound"));

function PageFallback() {
  return (
    <div className="space-y-4" aria-busy="true">
      <Skeleton className="h-8 w-56" />
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-28" />
        ))}
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}

export default function App() {
  const nav = useNavigate();
  const location = useLocation();

  // Global "/" focuses search: jump to the runs page when elsewhere.
  useHotkey(
    "/",
    useCallback(
      (e: KeyboardEvent) => {
        if (location.pathname !== "/runs") {
          e.preventDefault();
          nav("/runs", { state: { focusSearch: true } });
        }
      },
      [location.pathname, nav],
    ),
  );

  return (
    <AppShell>
      <ErrorBoundary>
        <Suspense fallback={<PageFallback />}>
          <Routes location={location}>
            <Route path="/" element={<Overview />} />
            <Route path="/runs" element={<Runs />} />
            <Route path="/runs/:id" element={<RunDetail />} />
            <Route path="/bench" element={<Bench />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </Suspense>
      </ErrorBoundary>
      <Toasts />
    </AppShell>
  );
}
