import type { ComponentProps } from "react";
import { buttonClass, type ButtonSize, type ButtonVariant } from "./button-styles";

export type { ButtonSize, ButtonVariant };

export interface ButtonProps extends ComponentProps<"button"> {
  variant: ButtonVariant;
  size?: ButtonSize;
}

/** A native button; `type` is "button" unless given, so it never submits a form by accident. */
export function Button({ variant, size = "md", type = "button", className, ...rest }: ButtonProps) {
  return <button type={type} className={buttonClass(variant, size, className)} {...rest} />;
}
