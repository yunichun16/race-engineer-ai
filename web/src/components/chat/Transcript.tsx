"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDownIcon } from "@/components/ui/icons";

/** How close to the bottom (px) still counts as reading the latest. */
const NEAR_BOTTOM = 80;

/** Scrolls `box` to its end and returns where it landed. */
function toBottom(box: HTMLElement): number {
  box.scrollTop = box.scrollHeight;
  return box.scrollTop;
}

export interface TranscriptProps {
  /**
   * Changes when a question is asked (the newest turn's id), and once when the page opens: the
   * view then goes to the bottom and follows the answer. Null for an empty conversation, which
   * reads from the top.
   */
  followKey: string | null;
  /** Changes when the reader opens something to read where it is (a corner chart under a card):
   *  the view stops following, so the chart growing below doesn't carry it away. */
  holdKey?: string | null;
  children: ReactNode;
}

/**
 * The conversation's scrolling column (760 px wide, centred). New content keeps the view at the
 * bottom only while the reader is within 80 px of it; a reader who has scrolled further up stays
 * put, with a "Jump to latest" button. Charts drawing late (their code loads on first use) count
 * as new content, through the size observer.
 *
 * Two things keep the reader's scrolling apart from the view's own moves:
 * - A scroll event at or below where the view last put itself is the view's own (the content may
 *   have grown again before the event arrived). Anything above it is the reader's: a wheel, a
 *   touch, keys, find in page, focus moving up the page.
 * - The column never shrinks within a task. A chart measures itself while it draws, with its root
 *   half built, and the browser would pull the view up to fit that moment's shorter page, as if
 *   the reader had scrolled. So the column keeps its height as a min-height, released and taken
 *   again once a frame after any change, which lets it really shrink (a retried answer losing
 *   its text) a frame late. The scroller has no scroll anchoring, which would move it silently.
 */
export function Transcript({ followKey, holdKey = null, children }: TranscriptProps) {
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const pinned = useRef(0);
  // An empty conversation has nothing to follow: it reads from the top, with no "Jump to latest".
  const empty = useRef(followKey === null);
  const [behind, setBehind] = useState(false);

  useEffect(() => {
    const box = scroller.current;
    const inner = content.current;
    if (!box || !inner) return;
    const near = () => box.scrollHeight - box.scrollTop - box.clientHeight <= NEAR_BOTTOM;
    const onScroll = () => {
      if (empty.current) return;
      if (following.current && box.scrollTop >= pinned.current - 1) {
        setBehind(false); // a new question went to the bottom (a no-op when already false)
        return;
      }
      following.current = near();
      setBehind(!following.current);
      if (following.current) pinned.current = box.scrollTop;
    };
    const resized = new ResizeObserver(() => {
      if (empty.current) return;
      if (following.current) pinned.current = toBottom(box);
      else setBehind(!near());
    });
    let frame = 0;
    const hold = () => {
      inner.style.minHeight = "";
      inner.style.minHeight = `${inner.offsetHeight}px`;
    };
    const changed = new MutationObserver(() => {
      if (frame === 0) {
        frame = requestAnimationFrame(() => {
          frame = 0;
          hold();
        });
      }
    });
    hold();
    box.addEventListener("scroll", onScroll, { passive: true });
    resized.observe(inner);
    resized.observe(box); // the composer growing, or the phone's address bar, resizes the view
    changed.observe(inner, { childList: true, subtree: true, characterData: true });
    return () => {
      box.removeEventListener("scroll", onScroll);
      resized.disconnect();
      changed.disconnect();
      cancelAnimationFrame(frame);
      inner.style.minHeight = "";
    };
  }, []);

  // A new question, or the conversation the page opened with: go to the latest and follow it.
  useLayoutEffect(() => {
    const box = scroller.current;
    if (!box) return;
    empty.current = followKey === null;
    following.current = true;
    if (empty.current) box.scrollTop = 0;
    else pinned.current = toBottom(box);
  }, [followKey]);

  useLayoutEffect(() => {
    if (holdKey !== null) following.current = false;
  }, [holdKey]);

  const jump = () => {
    const box = scroller.current;
    if (!box) return;
    following.current = true;
    pinned.current = toBottom(box);
    setBehind(false);
  };

  return (
    <div className="relative min-h-0 flex-1">
      {/* relative: visually hidden text (absolutely positioned) inside the transcript must take
          the scroller as its containing block, or it would hang below the page and make the
          whole document scroll. */}
      <div ref={scroller} className="relative h-full overflow-y-auto overscroll-contain [overflow-anchor:none]">
        <div ref={content} className="mx-auto flex w-full max-w-[760px] flex-col gap-6 px-4 pt-3 pb-6">
          {children}
        </div>
      </div>
      {behind && followKey !== null ? (
        <button
          type="button"
          onClick={jump}
          className="glass-nav absolute bottom-3 left-1/2 inline-flex min-h-11 -translate-x-1/2 cursor-pointer items-center gap-1.5 rounded-full px-4 text-sm font-medium text-fg"
        >
          Jump to latest
          <ChevronDownIcon className="size-4" />
        </button>
      ) : null}
    </div>
  );
}
