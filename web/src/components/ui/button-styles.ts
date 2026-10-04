// The look shared by Button and LinkButton (spec g). Primary is the lime fill with dark ink, in
// both themes: the one primary action per view (Ask, Send, a Try again that is the page's only
// action). Secondary is raised glass with a 3:1 outline; ghost is an underlined ink link. The
// series blue and orange stay reserved for drivers A and B.

export type ButtonVariant = "primary" | "secondary" | "ghost";
export type ButtonSize = "sm" | "md";

const BASE =
  "inline-flex items-center justify-center gap-2 font-semibold whitespace-nowrap " +
  "transition-colors duration-(--dur-hover) active:translate-y-px cursor-pointer " +
  "disabled:cursor-not-allowed disabled:opacity-50 disabled:active:translate-y-0";

const VARIANTS: Record<ButtonVariant, string> = {
  // On light glass the lime fill carries a thin olive edge (--accent-edge; transparent in dark).
  // Forced colours drop fills and shadows, so a border keeps the shape.
  primary:
    "bg-accent text-accent-ink no-underline shadow-[inset_0_0_0_1px_var(--accent-edge),inset_0_1px_0_rgb(255_255_255/.35)] " +
    "hover:bg-(--accent-hover) disabled:hover:bg-accent forced-colors:border forced-colors:border-[ButtonText]",
  secondary:
    "bg-glass-strong text-fg no-underline shadow-[inset_0_0_0_1px_var(--color-border-secondary),inset_0_1px_0_var(--highlight)] " +
    "hover:bg-raised disabled:hover:bg-glass-strong forced-colors:border forced-colors:border-[ButtonText]",
  ghost: "px-1 font-medium text-fg underline decoration-line-strong underline-offset-4 hover:decoration-current",
};

// Both sizes stay 44 px high on touch screens, the smallest comfortable target; sm drops to 36 px
// only under a fine pointer (a mouse). Ghost keeps its own 4 px side padding.
const SIZES: Record<ButtonSize, string> = {
  sm: "min-h-11 text-sm pointer-fine:min-h-9",
  md: "min-h-11 text-[15px]",
};

const PADDING: Record<ButtonSize, string> = {
  sm: "px-3.5 rounded-[10px]",
  md: "px-4.5 rounded-xl",
};

export function buttonClass(variant: ButtonVariant, size: ButtonSize = "md", extra?: string): string {
  return [BASE, VARIANTS[variant], SIZES[size], variant === "ghost" ? "rounded-xl" : PADDING[size], extra].filter(Boolean).join(" ");
}
