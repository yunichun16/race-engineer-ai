import type { ReactNode } from "react";
import { Num } from "@/components/findings/Num";
import { Reveal } from "@/components/motion/Reveal";
import { site } from "@/content/site";
import { SECTION, SectionHead } from "./SectionHead";

const STEPS: { title: string; body: ReactNode }[] = [
  {
    title: "Telemetry",
    body: (
      <>
        FastF1 timing and car data, about 4 samples a second, on a <Num id="grid" /> grid.
      </>
    ),
  },
  { title: "Corners", body: "Every lap cut into corner segments." },
  {
    title: "Model",
    body: "A Transformer masked autoencoder learns ordinary corners; Isolation Forest gives a second opinion.",
  },
  {
    title: "Explanations",
    body: (
      <>
        Each session&apos;s most unusual corners, its <Num id="flagShare" />, are compared with the driver&apos;s usual
        way through the turn, typed and costed; traffic is set aside.
      </>
    ),
  },
  {
    title: "Tools",
    body: "The same seven tools power this site, its chat and Claude, where they run as an MCP server.",
  },
];

const STACK = ["PyTorch", "ONNX", "DuckDB / Parquet", "FastAPI", "Claude Sonnet 5.5 tool use", "MCP Apps", "Next.js"];

/** "How it's built": the pipeline in five steps, the stack, and who built it. */
export function HowItWorks() {
  const { credit } = site;
  return (
    <section aria-labelledby="how-title" className={SECTION}>
      <SectionHead id="how-title" label="How it's built">
        From timing data to a sentence about one corner.
      </SectionHead>
      <Reveal>
        <ol className="grid lg:grid-cols-5 lg:gap-5">
          {STEPS.map((step, i) => (
            <li
              key={step.title}
              className="grid grid-cols-[40px_minmax(0,1fr)] content-start gap-x-3 gap-y-1 border-t border-hairline py-4.5 lg:grid-cols-1 lg:pb-0"
            >
              <span aria-hidden="true" className="pt-[3px] font-mono text-xs tracking-[0.1em] text-muted">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="font-semibold text-fg">{step.title}</span>
              <p className="col-start-2 text-sm/[1.6] text-muted lg:col-start-1">{step.body}</p>
            </li>
          ))}
        </ol>
      </Reveal>
      <ul aria-label="Built with" className="mt-5.5 flex flex-wrap gap-2">
        {STACK.map((name) => (
          <li key={name} className="rounded-full border border-edge px-3 py-1 font-mono text-xs whitespace-nowrap text-muted">
            {name}
          </li>
        ))}
      </ul>
      {credit ? (
        <p className="mt-4.5 flex flex-wrap items-center gap-x-2 text-sm text-muted">
          <span>Built by {credit.name}</span>
          {credit.links.map((link) => (
            <span key={link.href} className="inline-flex items-center gap-x-2">
              <span aria-hidden="true">·</span>
              <a href={link.href} className="link inline-flex min-h-11 items-center">
                {link.label}
              </a>
            </span>
          ))}
        </p>
      ) : null}
    </section>
  );
}
