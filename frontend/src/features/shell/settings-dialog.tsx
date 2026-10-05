"use client";

import { ComputerIcon, Moon02Icon, Sun03Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState, type ReactNode } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { changePolicy, getMe, getPolicy, updateCompany, type WritingLanguage } from "@/lib/api";
import { LOCALE_COOKIE, isLocale, localeNames, locales } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { applyTheme, readTheme, themes, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";

import { CompanyIdentity } from "./company-identity";
import { allowNotifications, canNotify } from "./notify";

// The chosen option is filled and in full ink; the others recede.
const SEGMENT =
  "text-muted-foreground data-[state=on]:bg-accent data-[state=on]:text-accent-foreground";

const themeIcons: Record<Theme, IconSvgElement> = {
  system: ComputerIcon,
  light: Sun03Icon,
  dark: Moon02Icon,
};

/**
 * Settings, opened from the account menu: how the app looks and reads on this device, and the
 * language Tamga writes in for the whole company.
 */
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
      <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto sm:max-w-lg">
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
        <NotificationsSection />
        <section className="flex flex-col">
          <h3 className="text-muted-foreground pb-1 text-xs font-medium">{t.settings.company}</h3>
          <Row label={t.settings.writesIn} hint={t.settings.writesInHint}>
            <WritingLanguagePicker />
          </Row>
        </section>
        <CompanyIdentitySection />
        <PaymentsSection />
      </DialogContent>
    </Dialog>
  );
}

/** Who the company is; its changes are the owner's. */
function CompanyIdentitySection() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  return <CompanyIdentity owner={me.data?.role === "owner"} />;
}

const RECORDED = ["per_invoice", "on_account"] as const;

/** How the company records payments in its books (ADR-0022): what the Books page shows of an
 * open invoice. A change is a new version of the policy; the payment path reads the firm's
 * account whatever it says. */
function PaymentsSection() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const policy = useQuery({ queryKey: ["policy"], queryFn: getPolicy, retry: false });
  const [tolerance, setTolerance] = useState<string | null>(null);
  const change = useMutation({
    // Only the field changed: the rest of the policy stays as it is in force when it is written.
    mutationFn: changePolicy,
    onSuccess: (updated, sent) => {
      queryClient.setQueryData(["policy"], updated);
      // A typed tolerance not saved yet stays while the other setting changes.
      if (sent.remainder_tolerance !== undefined) setTolerance(null);
      // What the Books page reads of each open invoice follows the policy.
      void queryClient.invalidateQueries({ queryKey: ["invoices"] });
      void queryClient.invalidateQueries({ queryKey: ["invoice"] });
    },
  });
  if (!policy.data) return null;
  const owner = me.data?.role === "owner";
  const words = t.settings.payments;
  const typed = tolerance ?? String(Number(policy.data.remainder_tolerance));
  const valid = /^\d{1,2}(\.\d{1,2})?$/.test(typed) && Number(typed) <= 20;

  return (
    <section className="flex flex-col">
      <h3 className="text-muted-foreground pb-1 text-xs font-medium">{words.title}</h3>
      <Row label={words.recorded} hint={words.recordedHint}>
        <ToggleGroup
          type="single"
          variant="outline"
          spacing={0}
          value={policy.data.payments_recorded}
          aria-label={words.recorded}
          disabled={!owner || change.isPending}
          onValueChange={(value) => {
            const chosen = RECORDED.find((each) => each === value);
            if (chosen && chosen !== policy.data?.payments_recorded) change.mutate({ payments_recorded: chosen });
          }}
        >
          {RECORDED.map((value) => (
            <ToggleGroupItem key={value} value={value} className={SEGMENT}>
              {words[value]}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </Row>
      <Row label={words.tolerance} hint={words.toleranceHint}>
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid) change.mutate({ remainder_tolerance: typed });
          }}
        >
          <span className="relative">
            <Input
              inputMode="decimal"
              value={typed}
              onChange={(event) => setTolerance(event.target.value)}
              disabled={!owner || change.isPending}
              aria-label={words.tolerance}
              aria-invalid={!valid}
              className="w-20 pe-6 text-end tabular-nums"
            />
            <span className="text-muted-foreground pointer-events-none absolute inset-y-0 end-2 flex items-center text-sm">
              %
            </span>
          </span>
          {tolerance !== null ? (
            <Button type="submit" size="sm" disabled={!owner || !valid || change.isPending}>
              {words.save}
            </Button>
          ) : null}
        </form>
      </Row>
      {me.data && !owner ? <span className="text-muted-foreground text-xs">{t.settings.ownerOnly}</span> : null}
      {change.isError ? (
        <span className="text-danger-foreground text-xs" role="alert">
          {t.errors.generic}
        </span>
      ) : null}
    </section>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="border-border flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t py-3 first-of-type:border-t-0">
      <span className="min-w-0 flex-1 basis-48 text-sm">
        {label}
        {hint ? <span className="text-muted-foreground block text-xs text-pretty">{hint}</span> : null}
      </span>
      {children}
    </div>
  );
}

/** Rendered only while the dialog is open, in the browser; a browser with no notifications has
 * nothing to show here. */
function NotificationsSection() {
  const { t } = useI18n();
  if (!canNotify()) return null;
  return (
    <section className="flex flex-col">
      <h3 className="text-muted-foreground pb-1 text-xs font-medium">{t.settings.notifications}</h3>
      <Row label={t.settings.notifyHere} hint={t.settings.notifyHint}>
        <BrowserNotifications />
      </Row>
    </section>
  );
}

/** The browser's own notification, asked for once: a browser that was refused never asks again. */
function BrowserNotifications() {
  const { t } = useI18n();
  const [permission, setPermission] = useState(() => Notification.permission);

  if (permission === "granted") {
    return <span className="text-muted-foreground text-sm">{t.settings.notifyOn}</span>;
  }
  if (permission === "denied") {
    return <span className="text-muted-foreground text-sm">{t.settings.notifyBlocked}</span>;
  }
  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => void allowNotifications().then(setPermission)}
    >
      {t.settings.notifyTurnOn}
    </Button>
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

/** The company's, not the device's: what Tamga writes from now on is in it, for every member. */
function WritingLanguagePicker() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const change = useMutation({
    mutationFn: (language: WritingLanguage) => updateCompany({ language }),
    onSuccess: (updated) => queryClient.setQueryData(["me"], updated),
  });
  const owner = me.data?.role === "owner";

  return (
    <div className="grid justify-items-end gap-1">
      <ToggleGroup
        type="single"
        variant="outline"
        spacing={0}
        value={me.data?.company.language ?? ""}
        aria-label={t.settings.writesIn}
        disabled={!owner || change.isPending}
        onValueChange={(value) => {
          if (isLocale(value) && value !== me.data?.company.language) change.mutate(value);
        }}
      >
        {locales.map((value) => (
          <ToggleGroupItem key={value} value={value} lang={value} className={SEGMENT}>
            {localeNames[value]}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>
      {me.data && !owner ? <span className="text-muted-foreground text-xs">{t.settings.ownerOnly}</span> : null}
      {change.isError ? (
        <span className="text-danger-foreground text-xs" role="alert">
          {t.errors.generic}
        </span>
      ) : null}
    </div>
  );
}
