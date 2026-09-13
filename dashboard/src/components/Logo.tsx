import clsx from "clsx";

/** Inline SVG shield + eye mark. */
export function Logo({ className, animated = false }: { className?: string; animated?: boolean }) {
  return (
    <svg viewBox="0 0 32 32" className={clsx("h-7 w-7", className)} aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id="sentinel-logo-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="1" stopColor="#c084fc" />
        </linearGradient>
      </defs>
      <path d="M16 2 4 7v8c0 7.5 5.1 13.2 12 15 6.9-1.8 12-7.5 12-15V7L16 2z" fill="url(#sentinel-logo-grad)" />
      <ellipse cx="16" cy="16" rx="7.5" ry="4.5" fill="none" stroke="#0b0d17" strokeWidth="2" />
      <circle cx="16" cy="16" r="2.4" fill="#0b0d17" className={animated ? "origin-center animate-pulse" : undefined} />
    </svg>
  );
}
