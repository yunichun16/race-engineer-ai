import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";
import { DetectorComparison } from "@/components/findings/DetectorComparison";
import { FindingCard } from "@/components/findings/FindingCard";
import { MethodSteps } from "@/components/findings/MethodSteps";
import { Num } from "@/components/findings/Num";
import { ProbeFlip } from "@/components/findings/ProbeFlip";
import { ShiftCurve } from "@/components/findings/ShiftCurve";
import { DocList } from "@/components/report/DocList";
import { ReportFigure } from "@/components/report/ReportFigure";
import { ReportToc } from "@/components/report/ReportToc";
import { CODE, cx } from "@/components/report/styles";
import { claimHref } from "@/content/claims";
import { site } from "@/content/site";
import { API_URL } from "@/lib/env";
import { FIGURES } from "@/lib/report/figures";
import type { TocItem } from "@/lib/report/toc";

export const metadata: Metadata = {
  title: "Report",
  description:
    "What the Race Engineer AI model found in F1 telemetry and where it falls short: the method, the baselines with intervals, the style probes, the 2026 rule change and the limits. Every number is sourced from the project's reports.",
};

// The page's sections, in order: the contents list and the h2s both come from here.
const SECTIONS = [
  { id: "short", title: "In 60 seconds", kicker: "The short version" },
  { id: "data", title: "The data", kicker: "What it learned from" },
  { id: "method", title: "The method", kicker: "How it works" },
  { id: "rq2", title: "Can it find mistakes?", kicker: "Research question 2" },
  { id: "checked", title: "A first human check", kicker: "Checked by hand" },
  { id: "rq1", title: "Can it tell drivers apart?", kicker: "Research question 1" },
  { id: "rq3", title: "What did the 2026 rules change?", kicker: "Research question 3" },
  { id: "ablations", title: "What else was tried", kicker: "Ablations and engineering" },
  { id: "limits", title: "Where it falls short", kicker: "Limits" },
  { id: "details", title: "Full reports", kicker: "Every report and figure" },
] as const;

type SectionId = (typeof SECTIONS)[number]["id"];

const TOC: TocItem[] = SECTIONS.map((s) => ({ id: s.id, text: s.title, level: 2 }));

function Section({ id, children }: { id: SectionId; children: ReactNode }) {
  const s = SECTIONS.find((x) => x.id === id)!;
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="border-t border-hairline pt-10 pb-12 first:border-t-0 first:pt-0 lg:pb-15">
      <p className="micro">{s.kicker}</p>
      <h2 id={`${id}-title`} className="mt-2 mb-6 font-serif text-[clamp(26px,3vw,36px)] leading-[1.1] tracking-[-0.01em] text-balance text-fg">
        {s.title}
      </h2>
      {children}
    </section>
  );
}

/** Running text: 17 px on a 72ch measure (spec g, inner pages). */
function Prose({ children }: { children: ReactNode }) {
  return <div className="grid max-w-[72ch] gap-5 text-[17px]/[1.7] text-fg">{children}</div>;
}

function MoreLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <p className="text-[17px]">
      <Link href={href} className="link inline-flex min-h-11 items-center gap-1 font-medium">
        {children}
        <span aria-hidden="true">→</span>
      </Link>
    </p>
  );
}

const LIMITS: readonly ReactNode[] = [
  <>
    The findings haven&apos;t been checked at scale yet: a larger human review is planned. Treat a finding as a lead to check against the
    replay, not a verdict.
  </>,
  <>
    The only labels are track-limits violations named by race control, a fraction of real mistakes. Most mistakes carry no label, so
    every score measured against the labels understates how often a flag is real, and every interval is wide.
  </>,
  <>
    The data is coarse, about <Num id="sampling" link />, so a braking point is only good to a sample and a brief lock-up can fall between
    two. The brake reading is on or off.
  </>,
  <>
    The position feed is smoothed (typical lateral offsets of <Num id="lateral" link />), so running wide shows up in speed and
    throttle rather than in the line, and the line through a corner isn&apos;t compared at all.
  </>,
  <>Types say how a corner differed from the driver&apos;s usual, not why.</>,
  <>
    Time lost stops <Num id="timeWindow" link /> after the apex, so exit mistakes are undercounted.
  </>,
  <>
    Traffic, yellow flags and energy management are not driver mistakes. In 2026 drivers lift and coast to recharge, often on team
    instructions, so coasting and braking-point differences can be strategy rather than style.
  </>,
  <>
    Style is relative to one teammate. A driver&apos;s numbers change when the teammate does, and drivers of different teams mix car
    and driver, so they can&apos;t be ranked from it.
  </>,
  <>
    One training run per variant: differences smaller than the intervals, or than the repeat-run noise (up to{" "}
    <Num id="repeatNoise" link /> in reconstruction error), are not findings.
  </>,
];

export default function FindingsPage() {
  return (
    <div className="mx-auto max-w-[1120px] px-4 pt-8 sm:px-6 sm:pt-12">
      <header className="max-w-[72ch]">
        <p className="micro">Report</p>
        <h1 className="mt-3 text-[clamp(36px,5vw,56px)] leading-[1.05] font-semibold tracking-tight text-balance text-fg">
          What the model found, and where it falls short
        </h1>
        <p className="mt-5 text-lead text-fg">
          A Transformer trained on <Num id="segments" link /> corners of F1 telemetry, checked against simple baselines. Every number
          links to the report it comes from.
        </p>
        <p className="mt-3 text-sm font-medium text-fg">{site.caveat}</p>
      </header>

      <div className="mt-8 sm:mt-12 lg:grid lg:grid-cols-[minmax(0,1fr)_15rem] lg:gap-x-12">
        <ReportToc items={TOC} variant="disclosure" className="mb-10" />
        <ReportToc items={TOC} variant="sidebar" className="lg:col-start-2 lg:row-start-1" />

        <div className="min-w-0 lg:col-start-1 lg:row-start-1">
          <Section id="short">
            <div className="grid gap-4 sm:grid-cols-2">
              <FindingCard
                tag="Mistakes"
                figure={<Num id="aurocAll" variant="figure" />}
                figureLabel="AUROC, Isolation Forest against the Transformer"
                title="The simple baseline ranks mistakes better overall."
                source={{ href: claimHref("aurocAll"), label: "Source: M3 summary" }}
              >
                With <Num id="ciLevel" /> intervals, <Num id="detAllIForest" /> against <Num id="detAllExit" />. But the Transformer
                is sharper at the top of its list: <Num id="top50" /> for Isolation Forest.
              </FindingCard>
              <FindingCard
                tag="Mistakes"
                figure={<Num id="combined" variant="figure" />}
                figureLabel="Average precision on the 2026 test events, the two together against either alone"
                title="Two detectors together do best, within wide intervals."
                source={{ href: claimHref("combined"), label: "Source: M4 summary" }}
              >
                The intervals overlap, and the base rate is <Num id="apBaseRate" />: labelled mistakes are rare, so every estimate is
                rough.
              </FindingCard>
              <FindingCard
                tag="Style"
                figure={<Num id="carCos" variant="figure" />}
                figureLabel="Median cosine between teammates' centroids"
                title="The embedding is mostly the car, so style is the gap between teammates."
                source={{ href: claimHref("carCos"), label: "Source: M4 style profiles" }}
              >
                Drivers of different teams sit at <Num id="carCosOther" />. Teammates still differ measurably, by
                small amounts: in <Num id="clearAny" /> of pair-seasons at least one of seven metrics differs clearly, against{" "}
                <Num id="clearChance" /> by chance.
              </FindingCard>
              <FindingCard
                tag="2026"
                figure={<Num id="shiftAll" variant="figure" />}
                figureLabel="More reconstruction error, 2025 to 2026"
                title="Most of the 2026 jump is new tracks, not new cars."
                source={{ href: claimHref("shiftAll"), label: "Source: M3 summary" }}
              >
                On the same four circuits in both years the rise is only <Num id="shiftSame" />, and fine-tuning on 2026 races barely
                moves it.
              </FindingCard>
            </div>
          </Section>

          <Section id="data">
            <Prose>
              <p>
                <Num id="sessions" link /> qualifying, sprint and race sessions of 2022-2026, from the Formula 1 live-timing data that
                FastF1 reads. The car data comes at about <Num id="sampling" link />; each lap is aligned on the
                distance around the track, resampled every <Num id="grid" link /> and cut into a window around every official turn:{" "}
                <Num id="segments" link /> corner segments.
              </p>
              <p>
                <Num id="labels" link /> of them carry a label: a lap time deleted for track limits, named by race control. Those are
                the only labels, so they measure the detectors against one kind of mistake, and the model itself never sees them.
              </p>
              <p>
                The split is by season, so the test is a new year: <Num id="splitFull" link />, the first year of the new cars.
              </p>
            </Prose>
            <div className="mt-5 flex flex-wrap gap-x-6">
              <MoreLink href="/report/dataset-card">The dataset card</MoreLink>
              <MoreLink href="/report/data-quality">Data quality</MoreLink>
            </div>
          </Section>

          <Section id="method">
            <Prose>
              <p>
                The model is a Transformer that learns what ordinary corners look like by filling in hidden stretches of them. A corner
                it can&apos;t fill in well is unusual. A simple baseline, Isolation Forest, gives a second opinion. The explanations
                (what the driver did differently, and what it cost) come from plain rules over the telemetry, not from the network.
              </p>
            </Prose>
            <div className="mt-8">
              <MethodSteps />
            </div>
          </Section>

          <Section id="rq2">
            <Prose>
              <p>
                Five scores were compared on the labelled mistakes. On all events Isolation Forest has the best AUROC (
                <Num id="aurocAll" link />
                ), but the Transformer&apos;s error after the apex puts more labelled mistakes at the very top of its list:{" "}
                <Num id="top50" link /> for Isolation Forest, and a higher average precision (<Num id="apAll" link />; the intervals
                overlap). On the 2026 test events alone the two tie on average precision (<Num id="apTie" link />).
              </p>
              <p>
                The planned score, the model&apos;s negative log-likelihood, is weak (AUROC <Num id="nll" link />): the model learns
                that corner exits vary, so it discounts exactly where most mistakes happen.
              </p>
            </Prose>
            <div className="my-8">
              <DetectorComparison />
            </div>
            <Prose>
              <p>
                Together the two do best: their average, chosen on the 2025 validation events only, reaches{" "}
                <Num id="combined" link /> for each on the test events, with a base rate of <Num id="apBaseRate" link />. The
                intervals overlap.
              </p>
              <p>
                Most flags are unusual, not costly. Of the <Num id="flags" link /> flags, <Num id="notCostly" link /> were not clearly
                slower than the driver&apos;s usual. <Num id="distinct" link /> distinct driving mistakes remain, and one costs{" "}
                <Num id="cost" link /> at the median.
              </p>
              <p>
                In races, the biggest losses were mostly other cars. A traffic rule now sets apart a loss that began with another car
                already close (being lapped, racing, held up). Among each race&apos;s top three findings, traffic fell from{" "}
                <Num id="traffic" link /> to <Num id="trafficAfter" link />. The pre-read that shaped the rule is the same one this
                measures, so it is a <Num id="devCheck" link />, not an evaluation.
              </p>
              <p>
                Known incidents are found when the detectors flag them, and often they don&apos;t: only <Num id="cuts" link />{" "}
                single-car &ldquo;leaving the track&rdquo; incidents were flagged at that turn. A cut usually saves time and, in a
                smoothed position feed, looks normal.
              </p>
            </Prose>
          </Section>

          <Section id="checked">
            <Prose>
              <p>
                One reviewer looked at the <Num id="reviewSize" /> highest-ranked findings of 2026, most of them against the session
                replay. That is too small a sample to quote an accuracy, so this site doesn&apos;t; a larger review is planned.
              </p>
            </Prose>
          </Section>

          <Section id="rq1">
            <Prose>
              <p>
                Within 2026, with the same cars in training and test, a linear probe on the Transformer&apos;s embedding names the team
                from a single corner <Num id="probeWithin" link /> of the time, against <Num id="probeWithinHand" link /> for
                hand-crafted features. Across seasons, where the cars change, the order flips: <Num id="probeAcross" link /> for the
                driver. The model never saw a team or driver label; it picked up how each car is driven.
              </p>
            </Prose>
            <div className="my-8">
              <ProbeFlip />
            </div>
            <Prose>
              <p>
                That makes the embedding mostly the car: teammates&apos; centroids have a median cosine of <Num id="carCos" link />,
                drivers of different teams <Num id="carCosOther" link />. So style here is the difference between teammates, who share a
                car, measured corner by corner, in the same session, on the same tyre compound.
              </p>
              <p>
                Teammates drive measurably differently, by small amounts. In <Num id="clearAny" link /> of pair-seasons at least one of
                seven metrics differs clearly, against <Num id="clearChance" link /> by chance, with median sizes of{" "}
                <Num id="sizes" link /> of braking point, <Num id="sizesSpeed" link /> of minimum speed and <Num id="sizesTime" link />{" "}
                per corner. Style persists into the next season (r = <Num id="persist" link /> for the braking point). In 2026, for
                example, Hamilton brakes earlier than Leclerc: by <Num id="hamBrakesEarlierText" link />.
              </p>
            </Prose>
            <div className="mt-5">
              <MoreLink href="/styles">Compare teammates</MoreLink>
            </div>
          </Section>

          <Section id="rq3">
            <Prose>
              <p>
                Reconstruction error rises <Num id="shiftAll" link /> from 2025 to 2026, but on the same four circuits in both years only{" "}
                <Num id="shiftSame" link />: most of the gap is the tracks, not the cars. Fine-tuning on 2026 races lowers the error on
                later 2026 races by <Num id="ftGain" link />, with no steady trend, while repeat runs of the same setting moved by up to{" "}
                <Num id="ftNoise" link />. Any benefit is <Num id="ftVerdict" link />.
              </p>
            </Prose>
            <div className="mt-8">
              <ShiftCurve />
            </div>
          </Section>

          <Section id="ablations">
            <Prose>
              <p>
                <Num id="variants" link /> of the masked autoencoder were trained, each on its own cloud GPU. Little matters:
                differences between them sit within the intervals. Removing the context tokens (tyres, fuel, traffic, weather,
                session) makes reconstruction only <Num id="contextTokens" link /> worse and leaves mistake detection unchanged, so the
                model makes little use of them.
              </p>
              <p>
                A CNN autoencoder with the same inputs reconstructs about as well, but its highest-scoring corners hold far fewer
                labelled mistakes (average precision <Num id="cnnAp" link /> on 2026), and it trained <Num id="cnnSlow" link /> slower:
                attention earns its place at the top of the list. Holding out whole circuits instead of seasons, detection on
                circuits the model never saw drops to an average precision of <Num id="unseen" link />. New tracks are a bigger
                shift than new cars.
              </p>
              <p>
                The scorer runs without PyTorch as an ONNX model, with the same answers: the largest relative difference was{" "}
                <Num id="onnxParity" link /> against a gate of <Num id="onnxGate" link />.
              </p>
            </Prose>
            <div className="mt-5 flex flex-wrap gap-x-6">
              <MoreLink href="/report/m3-ablations">The ablations</MoreLink>
              <MoreLink href="/report/m4-onnx">The ONNX export</MoreLink>
            </div>
          </Section>

          <Section id="limits">
            <ul className="max-w-[72ch] border-t border-hairline">
              {LIMITS.map((item, k) => (
                <li key={k} className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-2 border-b border-hairline py-3.5 text-[17px]/[1.7] text-fg">
                  <span className="font-mono text-muted" aria-hidden="true">
                    —
                  </span>
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </Section>

          <Section id="details">
            <Prose>
              <p>
                Two documents bring it all together: the technical report (the questions, the data, the method, every finding and the
                limits) and the model card (what the model is, what it is for and what it isn&apos;t).
              </p>
            </Prose>
            <div className="mt-5 mb-8 flex flex-wrap gap-x-6">
              <MoreLink href="/report/technical-report">Technical report</MoreLink>
              <MoreLink href="/report/model-card">Model card</MoreLink>
            </div>
            <DocList level={3} />
            <h3 className="mt-12 mb-5 text-title text-fg">Figures from the reports</h3>
            <div className="grid gap-8 sm:grid-cols-2">
              {FIGURES.map((f) => (
                <ReportFigure key={f.file} file={f.file} />
              ))}
            </div>
            <p className="mt-10 max-w-[72ch] text-[17px]/[1.7] text-muted">
              <strong className="font-semibold text-fg">Use it inside Claude:</strong> add{" "}
              <code className={cx(CODE, "break-all text-fg")}>{API_URL}/mcp</code> as a custom connector, with no sign-in.
            </p>
          </Section>
        </div>
      </div>
    </div>
  );
}
