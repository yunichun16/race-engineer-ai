"use client";

import { useEffect, useRef, type RefObject } from "react";

/**
 * Fades a section up once as it scrolls into view (spec f). Give the element the `reveal` class
 * and this ref; `<Reveal>` does both.
 *
 * Only an element that is below the fold when it mounts is hidden (the `pending` class), and only
 * until it enters, so without JavaScript or IntersectionObserver nothing is ever hidden, and
 * nothing already on screen at hydration blinks out and back. A deep link that lands past it
 * shows it too. It uses a root margin, not a threshold: a tall section never reaches 50% in view.
 * With reduced motion, globals.css shows `pending` elements at once. Never use it on an element
 * that contains position: sticky (a transform during the fade re-parents it).
 */
export function useReveal<T extends Element>(): RefObject<T | null> {
  const ref = useRef<T>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof IntersectionObserver === "undefined") return;
    if (element.getBoundingClientRect().top <= window.innerHeight) return;
    element.classList.add("pending");
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting || entry.boundingClientRect.bottom < 0) {
            entry.target.classList.remove("pending");
            observer.unobserve(entry.target);
          }
        }
      },
      { rootMargin: "0px 0px -10% 0px" },
    );
    observer.observe(element);
    return () => {
      observer.disconnect();
      // Never leave it hidden: a re-run (StrictMode, a remount) hides it again if it should be.
      element.classList.remove("pending");
    };
  }, []);
  return ref;
}
