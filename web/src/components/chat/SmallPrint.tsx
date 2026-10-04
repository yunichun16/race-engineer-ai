import { site } from "@/content/site";
import { PRIVACY_NOTE } from "./privacy";

/**
 * The chat's small print (plan 7.2), with the footer's lines, since /chat has no footer: what
 * answers, that it can be wrong, where the conversation lives, and the unofficial-project line;
 * with `privacy`, what the limits keep about a visitor too (M7, plan 3.4).
 * `short` is the one line a phone keeps under the composer once a conversation has started.
 */
export function SmallPrint({ short = false, privacy = true, className }: { short?: boolean; privacy?: boolean; className?: string }) {
  const classes = ["text-[12.5px]/[1.55] text-muted", className].filter(Boolean).join(" ");
  if (short) {
    return (
      <p className={classes}>
        Answers can be wrong; the numbers come from the tools, so check the charts. Unofficial fan project.
      </p>
    );
  }
  const lines = (
    <>
      Answers come from Claude Sonnet 5.5 using this project&apos;s tools. It can make mistakes; the numbers come from the
      tools, so check the charts. Conversations aren&apos;t stored on the server: this one lives in this tab. Up to 8
      questions per conversation. {site.unofficial} {site.caveat}
    </>
  );
  if (!privacy) return <p className={classes}>{lines}</p>;
  return (
    <div className={[classes, "flex flex-col gap-1.5"].join(" ")}>
      <p>{lines}</p>
      <p>{PRIVACY_NOTE}</p>
    </div>
  );
}
