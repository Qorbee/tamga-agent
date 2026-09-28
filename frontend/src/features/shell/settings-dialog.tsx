"use client";

import { ComputerIcon, Moon02Icon, Sun03Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { LOCALE_COOKIE, isLocale, localeNames, locales } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { applyTheme, readTheme, themes, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";

// The chosen option is filled and in full ink; the others recede.
const SEGMENT =
  "text-muted-foreground data-[state=on]:bg-accent data-[state=on]:text-accent-foreground";

const themeIcons: Record<Theme, IconSvgElement> = {
  system: ComputerIcon,
  light: Sun03Icon,
  dark: Moon02Icon,
};

/** The owner's own settings: the theme and the language. Opened from the account menu. */
export function SettingsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t.settings.title}</DialogTitle>
          <DialogDescription>{t.settings.description}</DialogDescription>
        </DialogHeader>
        <section className="flex flex-col">
          <h3 className="text-muted-foreground pb-1 text-xs font-medium">{t.settings.appearance}</h3>
          <Row label={t.settings.theme}>
            <ThemePicker />
          </Row>
          <Row label={t.settings.language}>
            <LanguagePicker />
          </Row>
        </section>
      </DialogContent>
    </Dialog>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="border-border flex items-center justify-between gap-4 border-t py-3 first-of-type:border-t-0">
      <span className="text-sm">{label}</span>
      {children}
    </div>
  );
}

function ThemePicker() {
  const { t } = useI18n();
  // Rendered only while the dialog is open, in the browser, where <html> carries the choice.
  const [theme, setTheme] = useState<Theme>(() =>
    readTheme(document.documentElement.dataset.themePreference),
  );

  return (
    <ToggleGroup
      type="single"
      variant="outline"
      spacing={0}
      value={theme}
      aria-label={t.settings.theme}
      onValueChange={(value) => {
        if (!value) return;
        const next = readTheme(value);
        setTheme(next);
        applyTheme(next);
      }}
    >
      {themes.map((value) => (
        <ToggleGroupItem key={value} value={value} className={cn(SEGMENT, "gap-1.5")}>
          <HugeiconsIcon icon={themeIcons[value]} strokeWidth={1.75} />
          {t.settings.themes[value]}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

function LanguagePicker() {
  const { t, locale } = useI18n();
  const router = useRouter();

  return (
    <ToggleGroup
      type="single"
      variant="outline"
      spacing={0}
      value={locale}
      aria-label={t.settings.language}
      onValueChange={(value) => {
        if (!isLocale(value) || value === locale) return;
        document.cookie = `${LOCALE_COOKIE}=${value}; path=/; max-age=31536000; samesite=lax`;
        // The server resolves the language, so it renders the app again in the new one.
        router.refresh();
      }}
    >
      {locales.map((value) => (
        <ToggleGroupItem key={value} value={value} lang={value} className={SEGMENT}>
          {localeNames[value]}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}
