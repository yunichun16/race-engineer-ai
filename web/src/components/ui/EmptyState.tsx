import type { ReactNode } from "react";

export interface EmptyStateProps {
  title: ReactNode;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}

/** Nothing to show, said plainly, with one thing that does work next (no dead ends). A dashed outline on the bare page, not glass. */
export function EmptyState({ title, children, action, className }: EmptyStateProps) {
  return (
    <div className={["rounded-card border border-dashed border-line-strong px-5 py-8 text-center sm:px-8", className].filter(Boolean).join(" ")}>
      <p className="font-semibold text-fg">{title}</p>
      <div className="mx-auto mt-2 max-w-[60ch] text-muted">{children}</div>
      {action ? <div className="mt-4 flex flex-wrap justify-center gap-2">{action}</div> : null}
    </div>
  );
}
