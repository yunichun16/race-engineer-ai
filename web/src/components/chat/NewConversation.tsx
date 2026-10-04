"use client";

import type { MouseEvent, RefObject } from "react";
import { Button } from "@/components/ui/Button";

export interface NewConversationProps {
  dialogRef: RefObject<HTMLDialogElement | null>;
  onConfirm: () => void;
}

/**
 * "Start a new conversation?": a native modal <dialog> (opened with showModal by its caller), so
 * the browser traps focus inside it and Esc or Cancel closes it with nothing lost. Focus starts on
 * Cancel, the safe choice.
 */
export function NewConversation({ dialogRef, onConfirm }: NewConversationProps) {
  const close = () => dialogRef.current?.close();
  // A click on the backdrop lands on the <dialog> itself, outside its inner panel.
  const onClick = (event: MouseEvent<HTMLDialogElement>) => {
    if (event.target === event.currentTarget) close();
  };
  return (
    <dialog
      ref={dialogRef}
      aria-labelledby="new-conversation-title"
      aria-describedby="new-conversation-body"
      onClick={onClick}
      className="glass-strong m-auto w-[min(100%-32px,26rem)] max-w-none rounded-card p-0 text-fg backdrop:bg-(--backdrop) backdrop:backdrop-blur-sm"
    >
      <div className="flex flex-col gap-2 p-5 sm:p-6">
        <h2 id="new-conversation-title" className="text-title">
          Start a new conversation?
        </h2>
        <p id="new-conversation-body" className="text-[15px]/[1.6] text-muted">
          This one will be cleared from this tab. The server keeps no copy of it.
        </p>
        <div className="mt-4 flex flex-wrap justify-end gap-2">
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={() => {
              close();
              onConfirm();
            }}
          >
            Start a new one
          </Button>
        </div>
      </div>
    </dialog>
  );
}
