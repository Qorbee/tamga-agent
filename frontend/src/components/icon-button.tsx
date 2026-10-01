import type { IconSvgElement } from "@hugeicons/react";
import { HugeiconsIcon } from "@hugeicons/react";
import type { ComponentProps } from "react";

import { Button } from "@/components/ui/button";

/**
 * A button that is only an icon. `label` names it for a screen reader and, as the tooltip, for
 * anyone unsure of the picture.
 */
export function IconButton({
  icon,
  label,
  size = "icon-sm",
  variant = "ghost",
  ...props
}: Omit<ComponentProps<typeof Button>, "children" | "aria-label" | "title"> & {
  icon: IconSvgElement;
  label: string;
}) {
  return (
    <Button {...props} size={size} variant={variant} aria-label={label} title={label}>
      <HugeiconsIcon icon={icon} strokeWidth={1.75} />
    </Button>
  );
}
