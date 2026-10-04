import type { ReactNode } from "react";
import { ErrorIcon, InfoIcon, WarningIcon } from "./icons";

export type NoticeTone = "info" | "warn" | "error";

export interface NoticeProps {
  tone: NoticeTone;
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}

const ICONS = {
  info: <InfoIcon className="size-5 text-muted" />,
  warn: <WarningIcon className="size-5 text-caution" />,
  error: <ErrorIcon className="size-5 text-caution" />,
} satisfies Record<NoticeTone, ReactNode>;

// Amber, never pink, for warnings and errors (pink means time lost): a 45% amber edge, and for an
// error a 3 px amber bar down the inside edge too.
const TONES: Record<NoticeTone, string> = {
  info: "",
  warn: "border-[color-mix(in_srgb,var(--color-text-warning)_45%,transparent)]",
  error:
    "border-[color-mix(in_srgb,var(--color-text-warning)_45%,transparent)] " +
    "shadow-[var(--shadow-glass-sm),inset_3px_0_0_var(--color-text-warning),inset_0_1px_0_var(--highlight)]",
};

/**
 * A message about the page's state, on glass. Info and warn are polite (`role="status"`), an
 * error interrupts (`role="alert"`). The title says it in words, so the colour never carries it
 * alone.
 */
export function Notice({ tone, title, children, action, className }: NoticeProps) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={["glass grid grid-cols-[20px_minmax(0,1fr)] gap-x-3 gap-y-1 rounded-[18px] px-4 py-3.5", TONES[tone], className]
        .filter(Boolean)
        .join(" ")}
    >
      <span className="mt-0.5">{ICONS[tone]}</span>
      <p className="min-w-0 font-semibold text-fg">{title}</p>
      {children ? <div className="col-start-2 min-w-0 text-[15px]/[1.6] text-muted">{children}</div> : null}
      {action ? <div className="col-start-2 mt-2 flex flex-wrap gap-2">{action}</div> : null}
    </div>
  );
}
