import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";
import { buttonClass, type ButtonSize, type ButtonVariant } from "./button-styles";

export interface LinkButtonProps extends Omit<ComponentProps<typeof Link>, "href" | "children"> {
  href: string;
  variant: ButtonVariant;
  size?: ButtonSize;
  children: ReactNode;
}

/** A link that looks like a button: for "go somewhere" actions such as "Open in the explorer". */
export function LinkButton({ href, variant, size = "md", className, children, ...rest }: LinkButtonProps) {
  return (
    <Link href={href} className={buttonClass(variant, size, className)} {...rest}>
      {children}
    </Link>
  );
}
