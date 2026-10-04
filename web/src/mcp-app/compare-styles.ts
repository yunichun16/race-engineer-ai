/**
 * The driving-style comparison (`../charts/compare-styles.ts`) as an MCP App inside Claude,
 * for the `compare_driving_styles` tool. This file is bundled into compare-styles.html
 * (`npm run build:mcp-app`), which the MCP server serves as a `ui://` resource.
 */
import { App } from "@modelcontextprotocol/ext-apps";
import "./frame.css";
import "../charts/styles/tokens.css";
import "../charts/styles/base.css";
import "../charts/styles/compare-styles.css";
import { argText, showStatus, widthObserver } from "../charts/dom.ts";
import {
  isStyleChart,
  render,
  type Plane,
  type StyleChart,
  type StyleKind,
} from "../charts/compare-styles.ts";
import { connect, wireApp } from "./host.ts";

// App wiring: everything below talks to the host.

const root = document.getElementById("app")!;
let current: StyleChart | undefined;
// The reader's choices, kept across redraws (a new width rebuilds the page).
let kind: StyleKind = "all";
let plane: Plane = "style";
let tableOpen = false;
const redraw = (data: StyleChart) =>
  render(root, data, {
    kind,
    onKindChange: (value) => {
      kind = value;
    },
    plane,
    onPlaneChange: (value) => {
      plane = value;
    },
    tableOpen,
    onTableToggle: (open) => {
      tableOpen = open;
    },
  });

const app = new App({ name: "race-engineer-compare-styles", version: "0.1.0" });

// Register handlers before connecting: the host may send the result right after the handshake.
wireApp(app, {
  isData: isStyleChart,
  onInput: (args) => {
    const drivers = [argText(args.driver_a), argText(args.driver_b)].filter(Boolean);
    const year = argText(args.year);
    const who = drivers.map((d) => d.toUpperCase()).join(" and ");
    const text =
      drivers.length === 2
        ? `Comparing how ${who} drive${year ? ` in ${year}` : ""}…`
        : "Comparing driving styles…";
    showStatus(root, text);
  },
  onCancel: () => showStatus(root, "Style comparison cancelled."),
  onResult: (data) => {
    current = data;
    redraw(data);
  },
  onError: (text) => {
    current = undefined;
    showStatus(root, text ?? "No style comparison in this result.");
  },
});

// Redraw for a new width only: opening the tables changes the height, not the charts.
widthObserver(root, () => current && redraw(current));

await connect(app);
