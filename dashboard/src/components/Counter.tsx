import { animate, useReducedMotion } from "framer-motion";
import { useEffect, useRef, useState } from "react";

/** Animated number that tweens between values. `format` renders the tweened value. */
export function Counter({ value, format = (v) => Math.round(v).toLocaleString(), duration = 0.9, className }: { value: number; format?: (v: number) => string; duration?: number; className?: string }) {
  const reduce = useReducedMotion();
  const [display, setDisplay] = useState(reduce ? value : 0);
  const prev = useRef(reduce ? value : 0);

  useEffect(() => {
    if (reduce) {
      setDisplay(value);
      prev.current = value;
      return;
    }
    const controls = animate(prev.current, value, {
      duration,
      ease: [0.22, 1, 0.36, 1],
      onUpdate: (v) => setDisplay(v),
      onComplete: () => {
        prev.current = value;
      },
    });
    return () => controls.stop();
  }, [value, duration, reduce]);

  return (
    <span className={className} aria-live="polite">
      {format(display)}
    </span>
  );
}
