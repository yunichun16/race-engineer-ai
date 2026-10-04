"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, type MouseEvent } from "react";
import { CloseIcon, MenuIcon } from "@/components/ui/icons";
import { site } from "@/content/site";
import { NAV_ITEMS, isCurrent } from "./nav";
import { ThemeToggle } from "./ThemeToggle";

// The width at which the header shows the full navigation again (Tailwind's `sm`).
const WIDE = "(min-width: 640px)";

/**
 * The phone menu (under 640 px): a "Menu" button that opens a native modal <dialog>, drawn as a
 * glass sheet floating in from the right. The browser traps focus inside it, closes it with Esc
 * and makes the page behind it inert; on close, focus goes back to the button. A link, the Close
 * button or a tap on the backdrop closes it too. The sheet's size, safe-area margin and slide-in
 * are the global .menu-sheet rule (globals.css).
 */
export function MobileMenu() {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const pathname = usePathname();

  // Widening the window past the phone layout hides the Menu button; don't leave an open modal
  // behind it.
  useEffect(() => {
    const wide = window.matchMedia(WIDE);
    const onChange = () => {
      if (wide.matches) dialogRef.current?.close();
    };
    wide.addEventListener("change", onChange);
    return () => wide.removeEventListener("change", onChange);
  }, []);

  const close = () => dialogRef.current?.close();

  // A click on the backdrop lands on the <dialog> itself, outside its inner panel.
  const onDialogClick = (event: MouseEvent<HTMLDialogElement>) => {
    if (event.target === event.currentTarget) close();
  };

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        aria-haspopup="dialog"
        onClick={() => dialogRef.current?.showModal()}
        className="inline-flex min-h-11 cursor-pointer items-center gap-1.5 rounded-full pr-3 pl-2.5 text-[15px] font-medium text-fg transition-colors duration-(--dur-hover) hover:bg-hairline max-[360px]:px-2.5"
      >
        <MenuIcon className="size-5 max-[379px]:hidden" />
        Menu
      </button>
      <dialog
        ref={dialogRef}
        aria-labelledby="mobile-menu-title"
        onClick={onDialogClick}
        onClose={() => buttonRef.current?.focus()}
        className="menu-sheet glass-strong rounded-panel text-fg backdrop:bg-(--backdrop) backdrop:backdrop-blur-sm"
      >
        <div className="flex min-h-full flex-col gap-5 px-3 pt-2 pb-[max(16px,env(safe-area-inset-bottom))]">
          <div className="flex min-h-13 items-center justify-between pl-2">
            <h2 id="mobile-menu-title" className="micro">
              Menu
            </h2>
            <button
              type="button"
              onClick={close}
              className="inline-flex size-11 cursor-pointer items-center justify-center rounded-full text-fg transition-colors duration-(--dur-hover) hover:bg-hairline"
            >
              <CloseIcon className="size-5" />
              <span className="sr-only">Close menu</span>
            </button>
          </div>
          <nav aria-label="Main">
            <ul className="grid gap-1">
              {NAV_ITEMS.map((item) => (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={close}
                    aria-current={isCurrent(pathname, item.href) ? "page" : undefined}
                    className={
                      "grid min-h-15 content-center gap-0.5 rounded-[18px] px-3.5 py-2.5 transition-colors duration-(--dur-hover) hover:bg-hairline " +
                      "aria-[current=page]:bg-glass-strong aria-[current=page]:shadow-[inset_0_1px_0_var(--highlight),inset_0_0_0_1px_var(--edge)] " +
                      "forced-colors:aria-[current=page]:outline-2 forced-colors:aria-[current=page]:outline-[Highlight]"
                    }
                  >
                    <span className="text-[17px] font-semibold">{item.label}</span>
                    <span className="text-sm text-muted">{item.description}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
          <div className="mt-auto grid gap-3.5">
            <ThemeToggle name="theme-menu" showLabels />
            <p className="text-sm text-muted">{site.shortCaveat}</p>
          </div>
        </div>
      </dialog>
    </>
  );
}
