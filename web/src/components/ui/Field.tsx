import type { ReactNode } from "react";

/** The id Field gives its hint, for the control's `aria-describedby`. */
export function hintId(htmlFor: string): string {
  return `${htmlFor}-hint`;
}

export interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  htmlFor: string;
  children: ReactNode;
  className?: string;
}

/**
 * A visible label (and optional hint) above any control. Give the control `id={htmlFor}` and,
 * when there is a hint, `aria-describedby={hintId(htmlFor)}`.
 */
export function Field({ label, hint, htmlFor, children, className }: FieldProps) {
  return (
    <div className={["flex min-w-0 flex-col gap-1.5", className].filter(Boolean).join(" ")}>
      <label htmlFor={htmlFor} className="text-sm font-medium text-fg">
        {label}
      </label>
      {hint ? (
        <p id={hintId(htmlFor)} className="text-sm text-muted">
          {hint}
        </p>
      ) : null}
      {children}
    </div>
  );
}

/**
 * The look of a text input or select, shared so every control lines up: raised glass with a 3:1
 * outline. 16 px text keeps iOS from zooming. The native option list follows color-scheme; its
 * options get the opaque glass colour where a platform paints them with the control's own fill.
 */
export const CONTROL_CLASS =
  "min-h-11 w-full rounded-xl border border-line-strong bg-glass-strong px-3 py-2 text-base text-fg " +
  "shadow-[inset_0_1px_0_var(--highlight)] placeholder:text-muted disabled:cursor-not-allowed disabled:opacity-60";
