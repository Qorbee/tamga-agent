import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { cn } from "@/lib/utils";

/** A person's initials in a circle: "Ayşe Kaya" is AK; an address alone gives its first letter. */
export function PersonAvatar({ name, className }: { name: string; className?: string }) {
  return (
    <Avatar className={cn("size-8", className)}>
      <AvatarFallback className="bg-secondary text-secondary-foreground text-xs font-medium">
        {initials(name)}
      </AvatarFallback>
    </Avatar>
  );
}

function initials(name: string): string {
  const words = name.includes("@") ? [name] : name.split(/\s+/).filter(Boolean);
  const letters = words.length > 1 ? [words[0], words[words.length - 1]] : words.slice(0, 1);
  return letters.map((word) => word?.[0] ?? "").join("").toLocaleUpperCase("tr") || "?";
}
