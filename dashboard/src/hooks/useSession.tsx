import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, getApiMode, onApiKeyChange, onApiModeChange, type ApiMode } from "@/lib/api";
import type { Health, Me, Role } from "@/lib/types";

interface Session {
  mode: ApiMode;
  me: Me | null;
  meError: string | null;
  health: Health | null;
  healthOk: boolean | null;
  can: (role: Role) => boolean;
  reloadMe: () => void;
}

const SessionContext = createContext<Session | null>(null);
const ROLE_RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };

export function SessionProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ApiMode>(getApiMode());
  const [me, setMe] = useState<Me | null>(null);
  const [meError, setMeError] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthOk, setHealthOk] = useState<boolean | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => onApiModeChange(setMode), []);
  useEffect(() => onApiKeyChange(() => setTick((t) => t + 1)), []);

  // who am I (re-run on key change)
  useEffect(() => {
    if (mode === "detecting") return;
    let alive = true;
    api
      .me()
      .then((m) => {
        if (alive) {
          setMe(m);
          setMeError(null);
        }
      })
      .catch((e: unknown) => {
        if (alive) {
          setMe(null);
          setMeError(e instanceof Error ? e.message : String(e));
        }
      });
    return () => {
      alive = false;
    };
  }, [mode, tick]);

  // health poll every 30s
  useEffect(() => {
    if (mode === "detecting") return;
    let alive = true;
    const check = () =>
      api
        .health()
        .then((h) => {
          if (alive) {
            setHealth(h);
            setHealthOk(h.status === "ok");
          }
        })
        .catch(() => {
          if (alive) setHealthOk(false);
        });
    void check();
    const t = setInterval(check, 30_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [mode]);

  const value = useMemo<Session>(
    () => ({
      mode,
      me,
      meError,
      health,
      healthOk,
      can: (role) => (me ? ROLE_RANK[me.role] >= ROLE_RANK[role] : false),
      reloadMe: () => setTick((t) => t + 1),
    }),
    [mode, me, meError, health, healthOk],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used within SessionProvider");
  return ctx;
}
