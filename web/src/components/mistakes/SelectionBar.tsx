"use client";

import { useEffect, useRef, type MouseEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/Button";
import { CloseIcon } from "@/components/ui/icons";

// The width from which the pickers sit inline on the page instead (Tailwind's `lg`).
const WIDE = "(min-width: 1024px)";

export interface SelectionBarProps {
  /** What is showing: "2026 Azerbaijan GP · Race · All drivers". */
  summary: string;
  /** The pickers, drawn in the sheet. */
  children: ReactNode;
}

/**
 * The phone's (under `lg`) one sticky strip under the header: what the page is showing and
 * Change, which opens the pickers in a native modal <dialog> drawn as a glass sheet (the phone
 * menu's sheet). The browser traps focus in it and closes it with Esc; a tap on the backdrop,
 * Close or Done closes it too, and focus goes back to the bar. Each change in the sheet applies
 * at once, so the results behind it are already loading when it closes.
 */
export function SelectionBar({ summary, children }: SelectionBarProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  // Widening past the phone layout hides the bar; don't leave an open modal behind it.
  useEffect(() => {
    const wide = window.matchMedia(WIDE);
    const onChange = () => {
      if (wide.matches) dialogRef.current?.close();
    };
    wide.addEventListener("change", onChange);
    return () => wide.removeEventListener("change", onChange);
  }, []);

  const close = () => dialogRef.current?.close();

  // A tap on the backdrop lands on the <dialog> itself, outside its inner panel.
  const onDialogClick = (event: MouseEvent<HTMLDialogElement>) => {
    if (event.target === event.currentTarget) close();
  };

  return (
    <>
      <div className="sticky top-[calc(var(--header-h)+8px)] z-30 lg:hidden">
        <button
          ref={buttonRef}
          type="button"
          aria-haspopup="dialog"
          onClick={() => dialogRef.current?.showModal()}
          className="glass-nav flex min-h-13 w-full cursor-pointer items-center gap-3 rounded-[18px] py-1.5 pr-1.5 pl-4 text-left text-fg"
        >
          <span className="min-w-0 flex-1 text-[15px]/[1.35] font-medium">{summary}</span>
          <span className="inline-flex min-h-10 shrink-0 items-center rounded-[12px] bg-glass-strong px-3.5 text-[15px] font-semibold shadow-[inset_0_0_0_1px_var(--color-border-secondary),inset_0_1px_0_var(--highlight)]">
            Change
          </span>
        </button>
      </div>
      <dialog
        ref={dialogRef}
        aria-labelledby="session-sheet-title"
        onClick={onDialogClick}
        onClose={() => buttonRef.current?.focus()}
        className="menu-sheet glass-strong rounded-panel text-fg backdrop:bg-(--backdrop) backdrop:backdrop-blur-sm"
      >
        <div className="flex min-h-full flex-col gap-5 px-4 pt-2 pb-[max(16px,env(safe-area-inset-bottom))]">
          <div className="flex min-h-13 items-center justify-between">
            <h2 id="session-sheet-title" className="micro">
              Choose a session
            </h2>
            <button
              type="button"
              onClick={close}
              className="-mr-1.5 inline-flex size-11 cursor-pointer items-center justify-center rounded-full text-fg transition-colors duration-(--dur-hover) hover:bg-hairline"
            >
              <CloseIcon className="size-5" />
              <span className="sr-only">Close</span>
            </button>
          </div>
          <div className="grid gap-5">{children}</div>
          <div className="mt-auto pt-2">
            <Button variant="primary" className="w-full" onClick={close}>
              Done
            </Button>
          </div>
        </div>
      </dialog>
    </>
  );
}
