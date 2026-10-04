"use client";

import { createElement, type HTMLAttributes, type ReactNode } from "react";
import { useReveal } from "./useReveal";

export interface RevealProps extends HTMLAttributes<HTMLElement> {
  as?: "div" | "section" | "header" | "article" | "aside" | "figure" | "li";
  className?: string;
  children: ReactNode;
}

/**
 * A block that fades up once as it scrolls into view (useReveal). For section heads, not for a
 * block that contains position: sticky (reveal the story's head, never the story grid).
 */
export function Reveal({ as = "div", className, children, ...rest }: RevealProps) {
  const ref = useReveal<HTMLElement>();
  // createElement rather than JSX: one ref type serves every tag in `as`.
  return createElement(as, { ...rest, ref, className: ["reveal", className].filter(Boolean).join(" ") }, children);
}
