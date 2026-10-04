/**
 * The lap comparison chart (`../charts/compare-laps.ts`) as an MCP App inside Claude, for the
 * `compare_laps` tool. This file is bundled into compare-laps.html (`npm run build:mcp-app`),
 * which the MCP server serves as a `ui://` resource.
 */
import { App } from "@modelcontextprotocol/ext-apps";
import "./frame.css";
import "../charts/styles/tokens.css";
import "../charts/styles/base.css";
import "../charts/styles/compare-laps.css";
import { showStatus, widthObserver } from "../charts/dom.ts";
import { isComparison, render, type Comparison } from "../charts/compare-laps.ts";
import { connect, wireApp } from "./host.ts";

// App wiring: everything below talks to the host.

const root = document.getElementById("app")!;
let current: Comparison | undefined;
let tableOpen = false; // kept across redraws, which rebuild the table
const redraw = (data: Comparison) =>
  render(root, data, {
    tableOpen,
    onTableToggle: (open) => {
      tableOpen = open;
    },
  });

const app = new App({ name: "race-engineer-compare-laps", version: "0.1.0" });

// Register handlers before connecting: the host may send the result right after the handshake.
wireApp(app, {
  isData: isComparison,
  onInput: (args) => {
    const drivers = [args.driver_a, args.driver_b].filter((d) => typeof d === "string");
    showStatus(root, `Comparing ${drivers.map((d) => d.toUpperCase()).join(" and ") || "laps"}…`);
  },
  onCancel: () => showStatus(root, "Comparison cancelled."),
  onResult: (data) => {
    current = data;
    redraw(data);
  },
  onError: (text) => {
    current = undefined;
    showStatus(root, text ?? "No lap comparison in this result.");
  },
});

// Redraw for a new width only: opening the corner table changes the height, not the chart.
widthObserver(root, () => current && redraw(current));

await connect(app);
