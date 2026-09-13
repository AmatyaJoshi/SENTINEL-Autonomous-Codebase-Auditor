import { Eye, EyeOff, KeyRound, Moon, Save, ShieldCheck, Sun, Trash2 } from "lucide-react";
import { useState } from "react";
import { api, getApiKey, setApiKey } from "@/lib/api";
import type { SettingsSnapshot } from "@/lib/types";
import { useAsync } from "@/hooks/useAsync";
import { useSession } from "@/hooks/useSession";
import { useTheme } from "@/hooks/useTheme";
import { useToast } from "@/hooks/useToast";
import { Card, ErrorNote, Skeleton } from "@/components/ui";

export default function Settings() {
  const { me, meError, mode, health, can, reloadMe } = useSession();
  const { theme, setTheme } = useTheme();
  const toast = useToast();
  const [key, setKey] = useState(getApiKey());
  const [show, setShow] = useState(false);
  const isAdmin = can("admin");
  const settings = useAsync<SettingsSnapshot>(() => api.settings(), [isAdmin, me?.name], { enabled: isAdmin });

  const save = () => {
    setApiKey(key.trim());
    reloadMe();
    toast.success(key.trim() ? "API key saved" : "API key cleared", "Sent as X-API-Key on every request.");
  };
  const clear = () => {
    setKey("");
    setApiKey("");
    reloadMe();
    toast.info("API key cleared");
  };

  return (
    <div className="space-y-5 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm muted mt-1">Identity, authentication, and appearance.</p>
      </div>

      <Card title="Who am I" subtitle="GET /api/v1/me">
        {me ? (
          <div className="flex items-center gap-3">
            <span className="h-10 w-10 rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 text-white text-sm font-bold flex items-center justify-center uppercase">{me.name.slice(0, 2)}</span>
            <div>
              <p className="font-medium">{me.name}</p>
              <p className="text-xs muted inline-flex items-center gap-1.5">
                <ShieldCheck className="h-3.5 w-3.5" /> role <span className="pill bg-indigo-500/10 text-indigo-700 dark:text-indigo-300">{me.role}</span>
              </p>
            </div>
            <div className="ml-auto text-right text-xs muted">
              <p>API mode: {mode}</p>
              {health && <p>server v{health.version}</p>}
            </div>
          </div>
        ) : meError ? (
          <ErrorNote message={meError} onRetry={reloadMe} />
        ) : (
          <Skeleton className="h-10" />
        )}
      </Card>

      <Card title="API key" subtitle={`Stored in localStorage as sentinel.apiKey and sent as X-API-Key`}>
        <div className="flex flex-col sm:flex-row gap-2">
          <div className="relative flex-1">
            <KeyRound className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 muted" />
            <input className="input pl-9 pr-10 font-mono" type={show ? "text" : "password"} value={key} onChange={(e) => setKey(e.target.value)} placeholder="Leave empty for open dev mode" aria-label="API key" autoComplete="off" spellCheck={false} onKeyDown={(e) => e.key === "Enter" && save()} />
            <button type="button" className="absolute right-2 top-1/2 -translate-y-1/2 btn-icon btn-ghost !p-1" onClick={() => setShow((s) => !s)} aria-label={show ? "Hide key" : "Show key"}>
              {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          <button className="btn-primary" onClick={save}>
            <Save className="h-4 w-4" /> Save
          </button>
          <button className="btn-outline" onClick={clear} disabled={!key}>
            <Trash2 className="h-4 w-4" /> Clear
          </button>
        </div>
        <p className="text-[11px] muted mt-2">Roles: viewer (read), operator (start/cancel runs, review), admin (settings, delete). Without keys configured, the server runs in open dev mode as admin.</p>
      </Card>

      <Card title="Appearance">
        <div className="flex gap-2" role="radiogroup" aria-label="Theme">
          {(
            [
              ["dark", "Dark", Moon],
              ["light", "Light", Sun],
            ] as const
          ).map(([t, label, Icon]) => (
            <button key={t} role="radio" aria-checked={theme === t} className={theme === t ? "btn-primary" : "btn-outline"} onClick={() => setTheme(t)}>
              <Icon className="h-4 w-4" /> {label}
            </button>
          ))}
        </div>
        <p className="text-[11px] muted mt-2">Preference is saved in localStorage (sentinel.theme). Reduced-motion is respected automatically.</p>
      </Card>

      {isAdmin && (
        <Card title="Server settings snapshot" subtitle="GET /api/v1/settings (redacted, admin only)">
          {settings.error ? (
            <ErrorNote message={settings.error} onRetry={settings.reload} />
          ) : settings.loading && !settings.data ? (
            <Skeleton className="h-40" />
          ) : settings.data ? (
            <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-white/10">
              <table className="w-full text-sm">
                <tbody>
                  {Object.entries(settings.data).map(([k, v]) => (
                    <tr key={k} className="border-b last:border-b-0 border-slate-200/70 dark:border-white/[0.06]">
                      <td className="px-3 py-2 font-mono text-xs muted whitespace-nowrap align-top">{k}</td>
                      <td className="px-3 py-2 font-mono text-xs break-all">{typeof v === "string" ? v : JSON.stringify(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </Card>
      )}
    </div>
  );
}
