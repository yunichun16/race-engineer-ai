/**
 * The corner explanation (`../charts/explain-corner.ts`) as an MCP App inside Claude, for the
 * `explain_corner` tool. This file is bundled into explain-corner.html
 * (`npm run build:mcp-app`), which the MCP server serves as a `ui://` resource.
 */
import { App } from "@modelcontextprotocol/ext-apps";
import "./frame.css";
import "../charts/styles/tokens.css";
import "../charts/styles/base.css";
import "../charts/styles/explain-corner.css";
import { argText, showStatus, widthObserver } from "../charts/dom.ts";
import {
  isCornerChart,
  render,
  unmount,
  type CornerChart,
  type ReplayState,
} from "../charts/explain-corner.ts";
import { connect, wireApp } from "./host.ts";

// App wiring: everything below talks to the host.

const root = document.getElementById("app")!;
let current: CornerChart | undefined;
let tableOpen = false; // kept across redraws, which rebuild the table
let replayState: Partial<ReplayState> = {}; // likewise the replay's frame, speed, view and play
// The replay plays once by itself, when it first comes into view; a redraw before that (a new
// width) keeps the offer open, and it lapses once the replay has played or been moved.
let autoplay = false;
const status = (text: string) => {
  unmount(root);
  showStatus(root, text);
};
const redraw = (data: CornerChart) => {
  try {
    render(root, data, {
      tableOpen,
      onTableToggle: (open) => {
        tableOpen = open;
      },
      replay: replayState,
      onReplayChange: (state) => {
        replayState = state;
        if (state.playing || state.frame > 0) autoplay = false;
      },
      autoplay,
    });
  } catch (error) {
    // A payload that passed the type guard but not `render`: say so rather than leave half a page.
    console.error(error);
    status("This corner explanation couldn't be drawn.");
  }
};

const app = new App({ name: "race-engineer-explain-corner", version: "0.1.0" });

// Register handlers before connecting: the host may send the result right after the handshake.
wireApp(app, {
  isData: isCornerChart,
  onInput: (args) => {
    const driver = argText(args.driver).toUpperCase();
    const corner = argText(args.corner).replace(/^T/i, "");
    const lap = argText(args.lap);
    const what = driver && corner ? `${driver}'s turn ${corner}` : "the corner";
    status(`Explaining ${what}${lap ? ` on lap ${lap}` : ""}…`);
  },
  onCancel: () => status("Corner explanation cancelled."),
  onResult: (data) => {
    current = data;
    replayState = {};
    autoplay = true;
    redraw(data);
  },
  onError: (text) => {
    current = undefined;
    status(text ?? "No corner explanation in this result.");
  },
});

// Redraw for a new width only: opening the table changes the height, not the chart.
widthObserver(root, () => current && redraw(current));

await connect(app);
