"use client";

import { ViewIcon, ViewOffSlashIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useState, type ComponentProps } from "react";

import { Input } from "@/components/ui/input";
import { useI18n } from "@/lib/i18n/client";
import { cn } from "@/lib/utils";

/** A password field with a button that shows what was typed, as a phone's keyboard hides it. */
export function PasswordInput({ className, ...props }: Omit<ComponentProps<typeof Input>, "type">) {
  const { t } = useI18n();
  const [shown, setShown] = useState(false);
  return (
    <div className="relative">
      <Input {...props} type={shown ? "text" : "password"} className={cn("pe-11", className)} />
      <button
        type="button"
        onClick={() => setShown((was) => !was)}
        aria-label={shown ? t.auth.hidePassword : t.auth.showPassword}
        aria-pressed={shown}
        className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 absolute inset-y-0 end-0 flex w-11 items-center justify-center rounded-e-xl outline-none focus-visible:ring-2"
      >
        <HugeiconsIcon icon={shown ? ViewOffSlashIcon : ViewIcon} strokeWidth={1.75} className="size-[18px]" aria-hidden />
      </button>
    </div>
  );
}
