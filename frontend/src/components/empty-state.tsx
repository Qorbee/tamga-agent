import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";

/** What a list page shows before it has data: what will appear here, and what makes it appear. */
export function EmptyState({
  icon,
  title,
  body,
}: {
  icon: IconSvgElement;
  title: string;
  body: string;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center pt-10 pb-[18vh] text-center">
      <div className="bg-secondary text-muted-foreground grid size-11 place-items-center rounded-2xl">
        <HugeiconsIcon icon={icon} strokeWidth={1.75} className="size-5" />
      </div>
      <h2 className="mt-5 text-base font-medium">{title}</h2>
      <p className="text-muted-foreground mt-1.5 max-w-[46ch] text-sm text-balance">{body}</p>
    </div>
  );
}
