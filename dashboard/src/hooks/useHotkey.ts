import { useEffect } from "react";

function isEditable(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

/** Global single-key shortcut (ignored while typing in a field). */
export function useHotkey(key: string, handler: (e: KeyboardEvent) => void, opts: { allowInInputs?: boolean } = {}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== key || e.metaKey || e.ctrlKey || e.altKey) return;
      if (!opts.allowInInputs && isEditable(e.target)) return;
      handler(e);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [key, handler, opts.allowInInputs]);
}
