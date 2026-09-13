import { AnimatePresence, motion } from "framer-motion";
import clsx from "clsx";
import { Activity, BarChart3, FlaskConical, LayoutDashboard, Menu, Moon, Settings, Sun, X } from "lucide-react";
import { useCallback, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { useSession } from "@/hooks/useSession";
import { useTheme } from "@/hooks/useTheme";
import { Logo } from "./Logo";
import { pageVariants, spring } from "./motion";
import { Dot } from "./ui";

const NAV = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/runs", label: "Runs", icon: Activity },
  { to: "/bench", label: "Benchmark", icon: BarChart3 },
  { to: "/settings", label: "Settings", icon: Settings },
];

function HealthDot() {
  const { healthOk, health, mode } = useSession();
  const color = healthOk === null ? "#94a3b8" : healthOk ? "#10b981" : "#f43f5e";
  const label = healthOk === null ? "Checking health" : healthOk ? `Healthy · v${health?.version ?? "?"}` : "Backend unreachable";
  const detail = health?.checks ? `db ${health.checks.db ?? "?"} · docker ${health.checks.docker ?? "?"} · llm ${health.checks.llm ?? "?"}` : "";
  return (
    <span className="inline-flex items-center gap-2 text-xs muted" title={`${label}${detail ? `\n${detail}` : ""}`} aria-label={label}>
      <Dot color={color} pulse={healthOk === true} />
      <span className="hidden md:inline">{healthOk === null ? "checking" : healthOk ? "healthy" : "offline"}</span>
      {mode === "mock" && (
        <span className="pill bg-fuchsia-500/15 text-fuchsia-700 dark:text-fuchsia-300 normal-case tracking-normal inline-flex items-center gap-1" title="Backend unreachable or VITE_MOCK=1: showing simulated data">
          <FlaskConical className="h-3 w-3" /> Demo data
        </span>
      )}
    </span>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { theme, toggle } = useTheme();
  const { me } = useSession();
  const location = useLocation();
  const [menu, setMenu] = useState(false);
  const closeMenu = useCallback(() => setMenu(false), []);

  return (
    <div className="min-h-screen flex flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[70] btn-primary">
        Skip to content
      </a>
      <header className="sticky top-0 z-40 glass border-x-0 border-t-0 rounded-none">
        <div className="mx-auto max-w-[1400px] px-4 sm:px-6 h-14 flex items-center gap-3">
          <NavLink to="/" className="flex items-center gap-2 shrink-0" aria-label="Sentinel home">
            <Logo />
            <span className="font-semibold tracking-tight text-[15px]">
              Sentinel
              <span className="hidden sm:inline text-xs font-normal muted ml-2">autonomous codebase auditor</span>
            </span>
          </NavLink>

          <nav className="hidden md:flex items-center gap-1 ml-4" aria-label="Primary">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) => clsx("relative px-3 py-1.5 rounded-lg text-sm font-medium transition-colors inline-flex items-center gap-1.5", isActive ? "text-indigo-600 dark:text-indigo-300" : "muted hover:text-slate-900 dark:hover:text-slate-100")}
              >
                {({ isActive }) => (
                  <>
                    <n.icon className="h-4 w-4" />
                    {n.label}
                    {isActive && <motion.span layoutId="nav-pill" className="absolute inset-0 -z-10 rounded-lg bg-indigo-500/10" transition={spring} />}
                  </>
                )}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2 sm:gap-3">
            <HealthDot />
            {me && (
              <span className="hidden sm:inline-flex items-center gap-1.5 text-xs muted" title={`Role: ${me.role}`}>
                <span className="h-6 w-6 rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 text-white text-[10px] font-bold flex items-center justify-center uppercase">{me.name.slice(0, 2)}</span>
                <span className="hidden lg:inline">{me.name}</span>
                <span className="pill bg-slate-500/10 text-slate-600 dark:text-slate-300">{me.role}</span>
              </span>
            )}
            <button className="btn-icon btn-ghost" onClick={toggle} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} title="Toggle theme">
              <AnimatePresence mode="wait" initial={false}>
                <motion.span key={theme} initial={{ rotate: -90, opacity: 0 }} animate={{ rotate: 0, opacity: 1 }} exit={{ rotate: 90, opacity: 0 }} transition={{ duration: 0.2 }} className="inline-flex">
                  {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                </motion.span>
              </AnimatePresence>
            </button>
            <button className="btn-icon btn-ghost md:hidden" onClick={() => setMenu((m) => !m)} aria-label="Toggle navigation" aria-expanded={menu}>
              {menu ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
            </button>
          </div>
        </div>
        <AnimatePresence>
          {menu && (
            <motion.nav initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="md:hidden overflow-hidden border-t border-slate-200/80 dark:border-white/[0.06]" aria-label="Primary mobile">
              <div className="px-4 py-2 flex flex-col">
                {NAV.map((n) => (
                  <NavLink key={n.to} to={n.to} end={n.end} onClick={closeMenu} className={({ isActive }) => clsx("px-3 py-2.5 rounded-lg text-sm font-medium inline-flex items-center gap-2", isActive ? "bg-indigo-500/10 text-indigo-600 dark:text-indigo-300" : "muted")}>
                    <n.icon className="h-4 w-4" /> {n.label}
                  </NavLink>
                ))}
              </div>
            </motion.nav>
          )}
        </AnimatePresence>
      </header>

      <main id="main" className="flex-1 mx-auto w-full max-w-[1400px] px-4 sm:px-6 py-6">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div key={location.pathname} variants={pageVariants} initial="initial" animate="animate" exit="exit">
            {children}
          </motion.div>
        </AnimatePresence>
      </main>

      <footer className="mx-auto w-full max-w-[1400px] px-4 sm:px-6 py-4 text-[11px] muted flex items-center justify-between">
        <span>Sentinel dashboard</span>
        <span>
          Press <kbd className="kbd">/</kbd> to search
        </span>
      </footer>
    </div>
  );
}
