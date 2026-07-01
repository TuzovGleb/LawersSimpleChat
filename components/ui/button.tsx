import * as React from "react"
import { Slot } from "@radix-ui/react-slot"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const buttonVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        destructive:
          "bg-destructive text-destructive-foreground hover:bg-destructive/90",
        outline:
          "border border-input bg-background hover:bg-accent hover:text-accent-foreground",
        secondary:
          "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-primary underline-offset-4 hover:underline",
        // Brand variants — exact replacements for the legacy globals.css
        // .btn-primary / .btn-outline-accent / .btn-secondary classes.
        brand:
          "border border-transparent bg-[var(--brand-accent)] text-white hover:bg-[var(--brand-accent-hover)] hover:text-white",
        brandOutline:
          "border border-[var(--brand-accent)] bg-transparent text-[var(--brand-accent)] hover:bg-[var(--brand-accent-bg)]",
        outlineMuted:
          "border border-[var(--border-strong)] bg-transparent text-[var(--text-primary)] hover:bg-white hover:border-[#cfd1d6]",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 rounded-md px-3",
        lg: "h-11 rounded-md px-8",
        icon: "h-10 w-10",
        // CTA sizes — pixel-parity with the legacy .btn / .btn-sm / .btn-lg
        // paddings so the desktop look is unchanged by the migration.
        cta: "gap-2.5 rounded-[8px] px-[22px] py-3.5 text-[16px] leading-none",
        ctaSm: "gap-2.5 rounded-[8px] px-4 py-2.5 text-sm leading-none",
        ctaLg: "gap-2.5 rounded-[8px] px-[26px] py-4 text-[17px] leading-none",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button, buttonVariants }
