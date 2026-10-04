import { Notice } from "@/components/ui/Notice";
import { unavailableCopy, type Unavailable } from "@/lib/chat/reducer";
import { ExplorerLinks } from "./ChatBanner";

export interface ChatUnavailableProps {
  unavailable: Unavailable;
  /** The page's clock (useNow), for "You can ask again in 23 min". */
  now: number;
  /** Whether saved answers are offered under the notice: null while that is still loading. */
  hasSaved: boolean | null;
}

/**
 * The chat can't take a question from this visitor for now: rate-limited, at today's budget,
 * paused, or unable to check its limits (plan 4.4). The site's own words, with the wait counted
 * down from the time the server gave (on screen; a screen reader hears the time itself); then
 * either the line that leads into the saved answers below, or, when there are none, the
 * explorers, so it is never a dead end. (The chat switched off keeps M6's ChatOff notice.)
 */
export function ChatUnavailable({ unavailable, now, hasSaved }: ChatUnavailableProps) {
  const copy = unavailableCopy(unavailable, now);
  // The notice is a live region: a screen reader gets the words that hold while it shows, and the
  // countdown is for the eye (the composer's bar, which isn't live, has it in words too).
  const title =
    copy.spoken === copy.title ? (
      copy.title
    ) : (
      <>
        <span aria-hidden="true">{copy.title}</span>
        <span className="sr-only">{copy.spoken}</span>
      </>
    );
  return (
    <Notice tone={copy.tone} title={title}>
      {hasSaved === true ? <p>{copy.saved}</p> : hasSaved === false ? <ExplorerLinks /> : null}
    </Notice>
  );
}
