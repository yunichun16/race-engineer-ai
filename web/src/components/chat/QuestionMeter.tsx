import { questionMeter } from "@/lib/chat/format";
import { dailyMeter, type QuotaState } from "@/lib/chat/reducer";

/**
 * "7 questions left in this conversation", from the last answer's `done`. It counts only what
 * the server counted: a stopped or cut-off question, or a corner opened with "Show telemetry",
 * leaves it as it was. Before the first answer it shows `fallback` (the server's state, if any).
 * Once 5 or fewer of the visitor's questions for today remain, and that comes first, it says
 * "3 questions left today" instead (M7's limits; `quota` from the status and each `done`).
 */
export function QuestionMeter({ left, fallback, quota = null }: { left: number | null; fallback?: string; quota?: QuotaState | null }) {
  const today = dailyMeter(quota, left);
  if (today !== null && (left !== null || fallback === undefined)) return <p className="micro min-w-0 flex-1">{today}</p>;
  if (left === null) return <p className="micro min-w-0 flex-1">{fallback}</p>;
  // A phone drops " in this conversation", so the count fits beside the buttons on one line.
  const text = questionMeter(left);
  const cut = text.indexOf(" in this conversation");
  return (
    <p className="micro min-w-0 flex-1">
      {cut === -1 ? (
        text
      ) : (
        <>
          {text.slice(0, cut)}
          <span className="max-sm:hidden">{text.slice(cut)}</span>
        </>
      )}
    </p>
  );
}
