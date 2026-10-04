/**
 * The mistake list and track map (`../charts/find-mistakes.ts`) as an MCP App inside Claude,
 * for the `find_mistakes` tool: the Explain button asks Claude about the corner. This file is
 * bundled into find-mistakes.html (`npm run build:mcp-app`), which the MCP server serves as a
 * `ui://` resource.
 */
import { App } from "@modelcontextprotocol/ext-apps";
import "./frame.css";
import "../charts/styles/tokens.css";
import "../charts/styles/base.css";
import "../charts/styles/find-mistakes.css";
import { argText, showStatus, widthObserver } from "../charts/dom.ts";
import {
  isMistakeList,
  render,
  type MistakeList,
  type RenderOptions,
  type ViewState,
} from "../charts/find-mistakes.ts";
import { connect, wireApp } from "./host.ts";

// App wiring: everything below talks to the host.

const root = document.getElementById("app")!;
let current: MistakeList | undefined;
let view: ViewState | undefined; // kept across redraws, which rebuild the page

const app = new App({ name: "race-engineer-find-mistakes", version: "0.1.0" });

/** Ask Claude about one mistake, when the host takes messages from the page. */
function explainOption(data: MistakeList): RenderOptions["onExplain"] {
  if (!app.getHostCapabilities()?.message) return undefined;
  return async (m) => {
    const text =
      `Explain ${m.driver}'s turn ${m.turn} on lap ${m.lap_number} at the ` +
      `${data.year} ${data.event} (${data.session_code}).`;
    const result = await app.sendMessage({ role: "user", content: [{ type: "text", text }] });
    return !result.isError;
  };
}

const redraw = (data: MistakeList) =>
  render(root, data, {
    onExplain: explainOption(data),
    view,
    onViewChange: (next) => {
      view = next;
    },
  });

// Register handlers before connecting: the host may send the result right after the handshake.
wireApp(app, {
  isData: isMistakeList,
  onInput: (args) => {
    const event = argText(args.event);
    const driver = typeof args.driver === "string" ? args.driver.trim().toUpperCase() : "";
    const whose = driver ? `${driver}'s ` : "";
    showStatus(root, `Finding ${whose}mistakes${event ? ` at ${event}` : ""}…`);
  },
  onCancel: () => showStatus(root, "Mistake search cancelled."),
  onResult: (data) => {
    current = data;
    view = undefined; // a new result starts from the whole list
    redraw(data);
  },
  onError: (text) => {
    current = undefined;
    showStatus(root, text ?? "No mistake list in this result.");
  },
});

// Redraw for a new width only: the layout and the map's scale follow the width.
widthObserver(root, () => current && redraw(current));

await connect(app);
