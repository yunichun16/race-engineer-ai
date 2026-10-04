/**
 * The race summary (`../charts/race-summary.ts`) as an MCP App inside Claude, for the
 * `get_race_summary` tool. This file is bundled into race-summary.html
 * (`npm run build:mcp-app`), which the MCP server serves as a `ui://` resource.
 */
import { App } from "@modelcontextprotocol/ext-apps";
import "./frame.css";
import "../charts/styles/tokens.css";
import "../charts/styles/base.css";
import "../charts/styles/race-summary.css";
import { argText, showStatus, widthObserver } from "../charts/dom.ts";
import { isRaceSummary, render, type RaceSummary } from "../charts/race-summary.ts";
import { connect, wireApp } from "./host.ts";

// App wiring: everything below talks to the host.

const root = document.getElementById("app")!;
let current: RaceSummary | undefined;
let tableOpen = false; // kept across redraws, which rebuild the table
const redraw = (data: RaceSummary) =>
  render(root, data, {
    tableOpen,
    onTableToggle: (open) => {
      tableOpen = open;
    },
  });

const app = new App({ name: "race-engineer-race-summary", version: "0.1.0" });

// Register handlers before connecting: the host may send the result right after the handshake.
wireApp(app, {
  isData: isRaceSummary,
  onInput: (args) => {
    const event = [argText(args.year), argText(args.event)].filter(Boolean).join(" ");
    const session = typeof args.session === "string" ? args.session.trim().toLowerCase() : "";
    const kind = session === "s" || session.startsWith("sprint") ? "sprint" : "race";
    const driver = typeof args.driver === "string" ? args.driver.trim().toUpperCase() : "";
    const what = [event, kind].filter(Boolean).join(" ");
    showStatus(root, `Summarising the ${what}${driver && ` for ${driver}`}…`);
  },
  onCancel: () => showStatus(root, "Race summary cancelled."),
  onResult: (data) => {
    current = data;
    redraw(data);
  },
  onError: (text) => {
    current = undefined;
    showStatus(root, text ?? "No race summary in this result.");
  },
});

// Redraw for a new width only: opening the table changes the height, not the chart.
widthObserver(root, () => current && redraw(current));

await connect(app);
