"use client";

import { claimHref } from "@/content/claims";
import { FigureFrame, NumbersTable } from "./FigureFrame";
import { PROBE_GROUPS, threeDecimals as f3, type ProbeFeature } from "./figure-data";
import { useMarks } from "./useMarks";
import { AxisTicks, Gridlines, Legend, LegendItem, LineKey, MARK_ROW, Marker, MarkerKey, Readout, linearPos, type Shape, type Tone } from "./parts";

const FEATURES: readonly { id: Exclude<ProbeFeature, "chance">; label: string; shape: Shape; tone: Tone }[] = [
  { id: "embedding", label: "Transformer embedding", shape: "circle", tone: "a" },
  { id: "handCrafted", label: "Hand-crafted features", shape: "square", tone: "b" },
];

const MAX = 0.5;
const TICKS = [0, 0.1, 0.2, 0.3, 0.4, 0.5];
const pos = (v: number) => linearPos(v, 0, MAX);

/**
 * The style probes: how often a linear probe names the right team or driver from a single corner,
 * from the Transformer's embedding against hand-crafted features, with chance marked. Within 2026
 * the embedding wins; across seasons, where the cars change, the order flips. Shares of corners as
 * m3_style_within_season.md and m3_results.md print them.
 */
export function ProbeFlip() {
  const { active, markProps } = useMarks(PROBE_GROUPS.length * FEATURES.length);
  const ticks = TICKS.map(pos);

  const readout = (() => {
    if (active === null) return null;
    const group = PROBE_GROUPS[Math.floor(active / FEATURES.length)];
    const feature = FEATURES[active % FEATURES.length];
    const row = group.rows[feature.id];
    return `${feature.label} (${group.label.toLowerCase()}): right on ${f3(row.est)} of corners, against ${f3(group.rows.chance.est)} by chance; ${group.detail}.`;
  })();

  return (
    <FigureFrame
      kicker="Style probes"
      title="The embedding knows the car: it wins within a season and loses across seasons"
      caption="The share of single corners for which a linear probe names the right team or driver. The dashed line is chance. Within 2026 the cars stay the same between training and test; across seasons they change, and the hand-crafted features come out ahead."
      legend={
        <Legend>
          {FEATURES.map((f) => (
            <LegendItem key={f.id} keyMark={<MarkerKey shape={f.shape} tone={f.tone} />}>
              {f.label}
            </LegendItem>
          ))}
          <LegendItem keyMark={<LineKey tone="muted" dashed />}>Chance</LegendItem>
        </Legend>
      }
      sources={[
        { href: claimHref("probeTeamRow"), label: "report/m3_style_within_season.md" },
        { href: claimHref("probeAcrossEmbRow"), label: "report/m3_results.md" },
      ]}
      numbers={
        <NumbersTable
          label="share of corners named correctly, by probe and features, with chance"
          head={["Probe", "Chance", "Transformer embedding", "Hand-crafted"]}
          numeric={[1, 2, 3]}
          rows={PROBE_GROUPS.map((g) => [g.label, f3(g.rows.chance.est), f3(g.rows.embedding.est), f3(g.rows.handCrafted.est)])}
        />
      }
    >
      <div role="group" aria-label="Style probes: share of corners named correctly. Use the arrow keys to move between them." className="grid gap-5">
        {PROBE_GROUPS.map((group, g) => {
          const chance = pos(group.rows.chance.est);
          return (
            <div key={group.id}>
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 px-2">
                <p className="text-sm font-semibold text-fg">{group.label}</p>
                <p className="font-mono text-xs text-muted tabular-nums">chance {f3(group.rows.chance.est)}</p>
              </div>
              <p className="px-2 text-xs/[1.5] text-muted">{group.detail}</p>
              <div className="mt-1.5 grid">
                {FEATURES.map((feature, k) => {
                  const row = group.rows[feature.id];
                  const line = feature.tone === "a" ? "stroke-series-a" : "stroke-series-b";
                  return (
                    <div key={feature.id} role="img" aria-label={`${group.label}, ${feature.label}`} className={`${MARK_ROW} px-2 pt-1.5`} {...markProps(g * FEATURES.length + k)}>
                      <div className="flex items-baseline justify-between gap-3 text-sm/[1.4]" aria-hidden="true">
                        <span className="text-fg">{feature.label}</span>
                        <span className="font-mono text-[13px] text-fg tabular-nums">{f3(row.est)}</span>
                      </div>
                      <svg className="block h-7 w-full overflow-visible" aria-hidden="true">
                        <Gridlines at={ticks} />
                        <line x1={`${chance}%`} x2={`${chance}%`} y1={0} y2="100%" className="stroke-muted" strokeWidth={1.5} strokeDasharray="3 3" />
                        <line x1="0%" x2={`${pos(row.est)}%`} y1="50%" y2="50%" className={line} strokeWidth={2} strokeLinecap="round" />
                        <Marker shape={feature.shape} tone={feature.tone} x={`${pos(row.est)}%`} y="50%" />
                      </svg>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
        <div className="px-2">
          <AxisTicks ticks={TICKS.map((t, k) => ({ at: ticks[k], label: t === 0 ? "0" : t.toFixed(1) }))} title="Share of corners named correctly" />
        </div>
      </div>
      <Readout text={readout} hint="Point at a row, or Tab to the figure and use the arrow keys, to compare it with chance." />
    </FigureFrame>
  );
}
