import type { ReactNode } from "react";

export interface CardProps {
  children: ReactNode;
  as?: "div" | "section" | "article" | "aside" | "li";
  className?: string;
}

/**
 * A glass card floating over the page: a stack of its children with 12 px between them. A page
 * changes its look only through `className` (the fan-project note passes its amber edge), so the
 * props stay as frozen.
 */
export function Card({ children, as: Tag = "div", className }: CardProps) {
  return (
    <Tag className={["glass grid content-start gap-3 rounded-card p-5 sm:p-6.5", className].filter(Boolean).join(" ")}>
      {children}
    </Tag>
  );
}
