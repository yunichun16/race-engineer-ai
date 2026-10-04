// The mini diverging bar of the landing's style card (spec h), for one teammate gap: zero in the
// middle, ±12 m across, a bar from zero to the estimate in driver A's blue and the interval as an
// ink whisker. Blue is the chart palette's series colour, used inside a figure only. The drawing
// stretches to the card's width (its strokes don't), and the words under it are HTML, so they stay
// 11 px however narrow the card is.

const BAR = { width: 300, zero: 150, domain: 12 } as const;

/** "−6.1": a true minus sign, and a plus kept for a positive difference. */
function signed(n: number): string {
  return n < 0 ? `−${Math.abs(n)}` : n > 0 ? `+${n}` : "0";
}

export interface MiniBarProps {
  est: number;
  lo: number;
  hi: number;
  /** What A minus B measures: "braking point". */
  metric: string;
  /** Driver A's code, for the drawing's name. */
  a: string;
  b: string;
  /** The words for each direction: "brakes earlier", "brakes later". */
  less: string;
  more: string;
}

export function MiniBar({ est, lo, hi, metric, a, b, less, more }: MiniBarProps) {
  const x = (m: number) => BAR.zero + (Math.max(-BAR.domain, Math.min(BAR.domain, m)) / BAR.domain) * BAR.zero;
  const [from, to] = [Math.min(x(est), BAR.zero), Math.max(x(est), BAR.zero)];
  const label =
    `${metric[0].toUpperCase()}${metric.slice(1)}, ${a} minus ${b}: ${signed(est)} m, interval ${signed(lo)} to ${signed(hi)} m: ` +
    `${a} ${est < 0 ? less : more}`;
  // The right-hand words drop the verb the left-hand ones already say: "brakes earlier … later".
  const moreShort = more.split(" ").at(-1);
  const ink = { stroke: "var(--color-text-primary)", strokeWidth: 1.5, vectorEffect: "non-scaling-stroke" } as const;
  return (
    <div role="img" aria-label={label} className="grid gap-1">
      <svg viewBox={`0 0 ${BAR.width} 30`} preserveAspectRatio="none" aria-hidden="true" className="block h-[30px] w-full overflow-visible">
        <line x1={BAR.zero} y1={1} x2={BAR.zero} y2={29} stroke="var(--color-border-secondary)" vectorEffect="non-scaling-stroke" />
        <rect x={from} y={9} width={to - from} height={12} fill="var(--a)" />
        <line x1={x(lo)} y1={15} x2={x(hi)} y2={15} {...ink} />
        <line x1={x(lo)} y1={9} x2={x(lo)} y2={21} {...ink} />
        <line x1={x(hi)} y1={9} x2={x(hi)} y2={21} {...ink} />
      </svg>
      <div aria-hidden="true" className="grid grid-cols-[1fr_auto_1fr] gap-x-2 font-mono text-[11px] leading-tight text-muted">
        <span>← {less}</span>
        <span>0 m</span>
        <span className="text-right">{moreShort} →</span>
      </div>
    </div>
  );
}
