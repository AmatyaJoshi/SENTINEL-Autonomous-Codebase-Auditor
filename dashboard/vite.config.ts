import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// base "/": FastAPI serves dist/ from "/" with an SPA fallback (any non-/api path
// returns index.html) and static assets under /assets, so absolute asset URLs are
// required for deep links like /runs/abc to resolve their chunks correctly.
export default defineConfig({
  base: "/",
  plugins: [react()],
  resolve: {
    // keep in sync with "paths" in tsconfig.app.json
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ["react", "react-dom", "react-router-dom"],
          motion: ["framer-motion"],
          charts: ["recharts"],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: false,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    exclude: ["node_modules", "dist", "e2e"],
    css: false,
    restoreMocks: true,
    unstubEnvs: true,
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/test/**", "src/**/*.test.{ts,tsx}", "src/main.tsx", "src/vite-env.d.ts"],
    },
  },
});
