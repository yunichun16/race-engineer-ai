/**
 * What the chat's limits keep about a visitor, in plain words (plan 3.4). It goes in /chat's
 * small print, the footer's fine print and the README, word for word. The counters live up to
 * 25 hours (the rolling hour, and the day's count until an hour after midnight UTC), hence
 * "within about a day".
 */
export const PRIVACY_NOTE =
  "To keep this free demo within budget, the server counts questions per connection using a scrambled, " +
  "daily-changing code made from your IP address, deleted within about a day. It stores no IP addresses, no " +
  "cookies and no questions; your conversation lives only in this browser tab. The hosting services (Vercel, " +
  "Modal) keep their own short-lived request logs.";
