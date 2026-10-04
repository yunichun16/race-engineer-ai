import { Fragment } from "react";
import { toBlocks, type InlineLine } from "@/lib/chat/format";

/** One line's pieces: plain text, **bold** and `code`, all as React text (never HTML). */
function Line({ line }: { line: InlineLine }) {
  return line.map((piece, i) => {
    if (piece.kind === "bold") {
      return (
        <strong key={i} className="font-semibold">
          {piece.text}
        </strong>
      );
    }
    if (piece.kind === "code") {
      return (
        <code key={i} className="rounded-md bg-raised px-1 py-0.5 font-mono text-[0.9em]">
          {piece.text}
        </code>
      );
    }
    return <Fragment key={i}>{piece.text}</Fragment>;
  });
}

/**
 * A piece of the answer's text: short paragraphs, bullets and numbered lists, with bold and code
 * inside a line (lib/chat/format.ts). The model is asked for plain text, so anything else stays
 * literal: "<script>" in an answer is shown as those eight characters.
 */
export function AnswerText({ text }: { text: string }) {
  const blocks = toBlocks(text);
  return (
    <div className="flex flex-col gap-3 text-base/[1.7] wrap-anywhere text-fg">
      {blocks.map((block, i) => {
        if (block.kind === "bullets") {
          return (
            <ul key={i} className="flex list-disc flex-col gap-1 pl-5 marker:text-muted">
              {block.items.map((item, j) => (
                <li key={j}>
                  <Line line={item} />
                </li>
              ))}
            </ul>
          );
        }
        if (block.kind === "numbered") {
          return (
            <ol key={i} start={block.start} className="flex list-decimal flex-col gap-1 pl-6 marker:text-muted">
              {block.items.map((item, j) => (
                <li key={j}>
                  <Line line={item} />
                </li>
              ))}
            </ol>
          );
        }
        return (
          <p key={i}>
            {block.lines.map((line, j) => (
              <Fragment key={j}>
                {j > 0 ? <br /> : null}
                <Line line={line} />
              </Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
