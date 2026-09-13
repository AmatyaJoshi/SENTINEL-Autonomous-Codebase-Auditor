import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { HashRouter } from "react-router-dom";
import { MotionConfig } from "framer-motion";
import App from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { SessionProvider } from "./hooks/useSession";
import { ToastProvider } from "./hooks/useToast";
import { initApi } from "./lib/api";
import "./index.css";

const root = createRoot(document.getElementById("root") as HTMLElement);

// Decide live vs mock before first render so the initial data fetches hit the right client.
initApi().finally(() => {
  root.render(
    <StrictMode>
      <ErrorBoundary>
        <MotionConfig reducedMotion="user">
          {/* HashRouter: works when FastAPI serves dist/ statically from "/" without SPA fallback routes. */}
          <HashRouter>
            <SessionProvider>
              <ToastProvider>
                <App />
              </ToastProvider>
            </SessionProvider>
          </HashRouter>
        </MotionConfig>
      </ErrorBoundary>
    </StrictMode>,
  );
});
