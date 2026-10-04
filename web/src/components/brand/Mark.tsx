import { BRAND, MARK } from "./brand";

export interface MarkProps {
  /**
   * "dot": the glowing apex alone, the site's logo in the header and footer lockups. "tile": the
   * navy app icon (the favicon and home-screen icon draw the same). "bare": the line and dot in
   * ink, for small inline use.
   */
  variant?: "dot" | "tile" | "bare";
  className?: string;
  /** An accessible name, when the mark stands alone; without it the mark is decorative. */
  title?: string;
}

/**
 * The brand mark: a racing line through a corner, with the lime apex. On the site the apex
 * stands alone as a glowing lime dot in both themes (the user's call, 2026-10-03); on the light
 * page it gets the same faint olive edge as a lime button (--accent-edge, transparent on dark)
 * so its shape still shows. The tile, line and dot together are the app icon, and the tile keeps
 * its fixed navy in both themes (it is the favicon's drawing). The bare mark draws in the text
 * colour with the accent-line dot (olive on light, lime on dark). Never recoloured, rotated or
 * animated.
 */
export function Mark({ variant = "tile", className, title }: MarkProps) {
  const a11y = title ? { role: "img", "aria-label": title } : { "aria-hidden": true };
  if (variant === "dot") {
    // The tile's apex and halo at the same 1:1.9 ratio, with a soft glow around the core.
    return (
      <svg
        viewBox="0 0 24 24"
        focusable="false"
        className={["shrink-0 overflow-visible", className].filter(Boolean).join(" ")}
        {...a11y}
      >
        <circle cx="12" cy="12" r="7.6" fill="var(--accent)" opacity={MARK.halo.opacity} />
        <circle
          cx="12"
          cy="12"
          r="4"
          fill="var(--accent)"
          stroke="var(--accent-edge)"
          strokeWidth="1"
          className="drop-shadow-[0_0_4px_var(--accent)]"
        />
      </svg>
    );
  }
  if (variant === "bare") {
    return (
      <svg viewBox="5 5 24 24" focusable="false" className={className} {...a11y}>
        <path d={MARK.line} fill="none" stroke="var(--color-text-primary)" strokeWidth={MARK.lineWidth} strokeLinecap="round" />
        <circle cx={MARK.apex.cx} cy={MARK.apex.cy} r={MARK.apex.r} fill="var(--accent-line)" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 32 32" focusable="false" className={["shrink-0", className].filter(Boolean).join(" ")} {...a11y}>
      <rect x="0.5" y="0.5" width="31" height="31" rx="8" fill={BRAND.tile} stroke={BRAND.edge} />
      <path d={MARK.line} fill="none" stroke={BRAND.line} strokeWidth={MARK.lineWidth} strokeLinecap="round" />
      <circle cx={MARK.apex.cx} cy={MARK.apex.cy} r={MARK.halo.r} fill={BRAND.apex} opacity={MARK.halo.opacity} />
      <circle cx={MARK.apex.cx} cy={MARK.apex.cy} r={MARK.apex.r} fill={BRAND.apex} />
    </svg>
  );
}
