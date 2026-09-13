import type { Config } from "tailwindcss";

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      colors: {
        surface: {
          50: "#f8fafc",
          100: "#f1f5f9",
          200: "#e2e8f0",
          800: "#1e2235",
          900: "#141726",
          950: "#0b0d17",
        },
      },
      boxShadow: {
        glow: "0 0 0 1px rgb(129 140 248 / 0.25), 0 10px 40px -10px rgb(99 102 241 / 0.45)",
        card: "0 1px 2px rgb(0 0 0 / 0.04), 0 8px 24px -12px rgb(15 23 42 / 0.18)",
      },
      keyframes: {
        shimmer: { "0%": { backgroundPosition: "-200% 0" }, "100%": { backgroundPosition: "200% 0" } },
        flow: { "0%": { strokeDashoffset: "24" }, "100%": { strokeDashoffset: "0" } },
        pulseRing: { "0%": { transform: "scale(0.9)", opacity: "0.8" }, "100%": { transform: "scale(1.6)", opacity: "0" } },
      },
      animation: {
        shimmer: "shimmer 1.6s linear infinite",
        flow: "flow 0.8s linear infinite",
        pulseRing: "pulseRing 1.4s ease-out infinite",
      },
    },
  },
  plugins: [],
} satisfies Config;
