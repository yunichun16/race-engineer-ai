import Link from "next/link";
import { Notice } from "@/components/ui/Notice";
import { COPY } from "@/lib/chat/reducer";

/** Fake mode (plan 6.6, 8.7): the server's chat is scripted, while the tools run on real data. */
export function ChatBanner() {
  return (
    <Notice tone="warn" title="Development mode: answers are scripted and no model is called.">
      <p>The tools and charts use the real data.</p>
    </Notice>
  );
}

export const CHAT_OFF_TITLE = COPY.chatOff;

const EXPLORERS = [
  { href: "/mistakes", label: "Mistakes", description: "flagged corners, race by race" },
  { href: "/styles", label: "Styles", description: "how teammates drive differently" },
  { href: "/report", label: "Report", description: "method, results and limits" },
] as const;

/** "The explorers answer the same questions:" and the three links, for wherever the chat can't
 *  answer at all (plan 6.6 and 8.8: no dead end). */
export function ExplorerLinks() {
  return (
    <>
      <p>The explorers answer the same questions:</p>
      <ul className="mt-2 flex flex-col gap-1">
        {EXPLORERS.map((item) => (
          <li key={item.href}>
            <Link href={item.href} className="link inline-flex min-h-11 items-center font-medium pointer-fine:min-h-8">
              {item.label}
            </Link>
            <span>: {item.description}</span>
          </li>
        ))}
      </ul>
    </>
  );
}

/**
 * The chat is switched off on this server (health or the chat's status says so, or a question
 * was refused with `chat_disabled`). No dead end: the explorers answer the same questions without
 * it, and the page offers saved answers under it (M7).
 */
export function ChatOff() {
  return (
    <Notice tone="info" title={CHAT_OFF_TITLE}>
      <ExplorerLinks />
    </Notice>
  );
}
