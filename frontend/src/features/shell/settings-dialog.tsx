"use client";

import {
  ArrowLeft01Icon,
  ArrowRight01Icon,
  Brain02Icon,
  Building03Icon,
  ComputerIcon,
  Moon02Icon,
  Settings02Icon,
  Sun03Icon,
  Wallet01Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore, type ReactNode } from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { MemorySection } from "@/components/memory";
import { Segmented } from "@/components/segmented";
import {
  copyOf,
  MethodsField,
  PayDayField,
  ReserveChoices,
  ReserveEditor,
  Row,
  usePolicyChange,
} from "@/components/policy-settings";
import {
  changePolicy,
  getMe,
  getPolicy,
  getReferenceCurrencies,
  getReservePresets,
  updateCompany,
  type ReserveRule,
  type WritingLanguage,
} from "@/lib/api";
import { LOCALE_COOKIE, isLocale, localeNames, locales } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/client";
import { applyTheme, readTheme, themes, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";

import { CompanyIdentity } from "./company-identity";
import { allowNotifications, canNotify } from "./notify";

const themeIcons: Record<Theme, IconSvgElement> = {
  system: ComputerIcon,
  light: Sun03Icon,
  dark: Moon02Icon,
};

const TABS = ["general", "company", "payments", "memory"] as const;
const tabIcons: Record<(typeof TABS)[number], IconSvgElement> = {
  general: Settings02Icon,
  company: Building03Icon,
  payments: Wallet01Icon,
  memory: Brain02Icon,
};

// Where the parts become a side menu (Tailwind's `sm`): below it they are a page of their own.
const SIDE_MENU = "(min-width: 640px)";

function onWidth(onChange: () => void) {
  const query = window.matchMedia(SIDE_MENU);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** Whether the parts are laid out down the side, so the arrow keys follow what is seen. */
function useSideMenu(): boolean {
  return useSyncExternalStore(
    onWidth,
    () => window.matchMedia(SIDE_MENU).matches,
    () => true,
  );
}

type Part = (typeof TABS)[number];

/**
 * Settings, opened from the account menu: a menu down the left and the chosen part beside it, as
 * the chat apps the owner knows lay theirs out. On a phone it is a page of its own: a list of the
 * parts, each with what it holds now, and a tap opens one with a way back. How the app looks and
 * reads on this device; the company, and the language Tamga writes in for it; how it pays (the
 * ways, the reserve, the pay day) and how its books record payments; and what Tamga remembers.
 */
export function SettingsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useI18n();
  const side = useSideMenu();
  const [part, setPart] = useState<Part>("general");
  // On a phone, whether a part is open over the list; each opening starts at the list.
  const [inPart, setInPart] = useState(false);
  // Opened from outside (the account menu, a shortcut), the dialog's own callback never fires.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setInPart(false);
  }
  const listed = !side && !inPart;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={side || !inPart}
        className="flex flex-col gap-0 overflow-hidden p-0 max-sm:inset-0 max-sm:h-(--app-height,100dvh) max-sm:max-w-none max-sm:translate-x-0 max-sm:translate-y-0 max-sm:rounded-none max-sm:ring-0 sm:h-[min(44rem,calc(100dvh-2rem))] sm:max-w-4xl"
      >
        <DialogHeader className="sr-only">
          <DialogTitle>{t.settings.title}</DialogTitle>
          <DialogDescription>{t.settings.description}</DialogDescription>
        </DialogHeader>
        {!side ? (
          <header className="flex h-14 shrink-0 items-center gap-2 px-3">
            {inPart ? (
              <button
                type="button"
                onClick={() => setInPart(false)}
                aria-label={t.settings.back}
                className="hover:bg-accent flex size-10 items-center justify-center rounded-full"
              >
                <HugeiconsIcon icon={ArrowLeft01Icon} strokeWidth={1.75} className="size-5" />
              </button>
            ) : (
              <span className="size-10" aria-hidden />
            )}
            <h2 className="flex-1 text-center text-base font-medium">{inPart ? t.settings.tabs[part] : t.settings.title}</h2>
            <span className="size-10" aria-hidden />
          </header>
        ) : null}
        {listed ? <PartList onPick={(chosen) => {
          setPart(chosen);
          setInPart(true);
        }} /> : null}
        <Tabs
          value={part}
          onValueChange={(value) => setPart(value as Part)}
          orientation={side ? "vertical" : "horizontal"}
          className={cn("min-h-0 flex-1 gap-0 sm:flex-row", listed && "hidden")}
        >
          <nav aria-label={t.settings.title} className="border-border shrink-0 max-sm:hidden sm:w-56 sm:border-e">
            <p className="text-muted-foreground px-5 pt-5 pb-2 text-xs font-medium">{t.settings.title}</p>
            <TabsList className="w-full flex-col items-stretch justify-start gap-1 rounded-none bg-transparent p-3 pt-0">
              {TABS.map((tab) => (
                <TabsTrigger
                  key={tab}
                  value={tab}
                  className="text-muted-foreground hover:text-foreground data-[state=active]:bg-segment data-[state=active]:text-foreground w-full flex-none justify-start gap-2.5 rounded-xl px-3 py-2 text-sm font-normal data-[state=active]:shadow-none"
                >
                  <HugeiconsIcon icon={tabIcons[tab]} strokeWidth={1.75} />
                  {t.settings.tabs[tab]}
                </TabsTrigger>
              ))}
            </TabsList>
          </nav>
          {/* Every part stays mounted, hidden when not chosen: a draft typed in one survives a look
              at another, and going back to the list on a phone. */}
          <div className="min-h-0 flex-1 overflow-y-auto px-5 pt-2 pb-8 sm:px-8 sm:pt-12 sm:pb-5">
            <TabsContent forceMount value="general" className="grid gap-8 data-[state=inactive]:hidden">
              <Part title={t.settings.appearance}>
                <Row label={t.settings.theme}>
                  <ThemePicker />
                </Row>
                <Row label={t.settings.language}>
                  <LanguagePicker />
                </Row>
              </Part>
              <NotificationsSection />
            </TabsContent>
            <TabsContent forceMount value="company" className="grid gap-8 data-[state=inactive]:hidden">
              <Part title={t.settings.company}>
                <Row label={t.settings.writesIn} hint={t.settings.writesInHint}>
                  <WritingLanguagePicker />
                </Row>
              </Part>
              <CompanyIdentitySection />
            </TabsContent>
            <TabsContent forceMount value="payments" className="grid gap-8 data-[state=inactive]:hidden">
              <HowYouPaySection />
              <PaymentsSection />
            </TabsContent>
            <TabsContent forceMount value="memory" className="data-[state=inactive]:hidden">
              <MemorySection />
            </TabsContent>
          </div>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

/** The parts as a phone lists them: grouped rows, each with its icon and what it holds now. */
function PartList({ onPick }: { onPick: (part: Part) => void }) {
  const { t, locale } = useI18n();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const theme = readTheme(document.documentElement.dataset.themePreference);
  const holds: Record<Part, string> = {
    general: `${t.settings.themes[theme]} · ${localeNames[locale]}`,
    company: me.data?.company.name ?? "",
    payments: t.settings.holds.payments,
    memory: t.settings.holds.memory,
  };
  return (
    <nav aria-label={t.settings.title} className="min-h-0 flex-1 overflow-y-auto px-4 pb-8">
      <ul className="bg-card divide-border divide-y overflow-hidden rounded-2xl">
        {TABS.map((tab) => (
          <li key={tab}>
            <button
              type="button"
              onClick={() => onPick(tab)}
              className="hover:bg-accent/50 active:bg-accent flex w-full items-center gap-4 px-4 py-3.5 text-start"
            >
              <HugeiconsIcon icon={tabIcons[tab]} strokeWidth={1.75} className="text-muted-foreground size-6 shrink-0" />
              <span className="min-w-0 flex-1">
                <span className="block text-base">{t.settings.tabs[tab]}</span>
                {holds[tab] ? <span className="text-muted-foreground block truncate text-sm">{holds[tab]}</span> : null}
              </span>
              <HugeiconsIcon icon={ArrowRight01Icon} strokeWidth={1.75} className="text-muted-foreground size-4 shrink-0" />
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** A part of a settings page: its heading, then its rows. */
function Part({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col">
      <h3 className="pb-2 text-base font-medium">{title}</h3>
      {children}
    </section>
  );
}

/** Who the company is; its changes are the owner's. */
function CompanyIdentitySection() {
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  return <CompanyIdentity owner={me.data?.role === "owner"} />;
}

/** How the company pays (ADR-0027): the ways, the limit and its currency, the reserve, the pay
 * day. Each change is a new version of the policy, with only that field changed. */
function HowYouPaySection() {
  const { t } = useI18n();
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const policy = useQuery({ queryKey: ["policy"], queryFn: getPolicy, retry: false });
  const presets = useQuery({ queryKey: ["reserve-presets"], queryFn: getReservePresets, staleTime: Infinity });
  const currencies = useQuery({ queryKey: ["reference-currencies"], queryFn: getReferenceCurrencies, staleTime: Infinity });
  const [limit, setLimit] = useState<string | null>(null);
  // The rule open in the editor, and whether it is a copy (saved and put in use) or one of the
  // owner's changed (saved, and in use only if it was).
  const [editing, setEditing] = useState<{ rule: ReserveRule; copy: boolean } | null>(null);
  const change = usePolicyChange((sent) => {
    if (sent.approval_limit !== undefined) setLimit(null);
    if (sent.save_rule) setEditing(null);
  });
  if (!policy.data) return null;
  const owner = me.data?.role === "owner";
  const words = t.settings.howYouPay;
  const current = policy.data;
  const typed = limit ?? String(Number(current.approval_limit));
  const valid = /^\d{1,12}(\.\d{1,2})?$/.test(typed);
  const own = current.reserve_rules;
  // One rule at a time, merged on the server with the list in force: another screen's rule stays.
  const save = (rule: ReserveRule) =>
    change.mutate(editing?.copy ? { save_rule: rule, reserve_rule: rule.key } : { save_rule: rule });

  return (
    <section className="flex flex-col">
      <h3 className="pb-2 text-base font-medium">{words.title}</h3>
      <MethodsField
        methods={current.methods}
        first={current.default_method}
        disabled={!owner || change.isPending}
        onChange={(methods, first) => change.mutate({ methods, default_method: first })}
      />
      <Row label={words.currency} hint={words.currencyHint}>
        {/* What the company's reference rates convert, from its country's pack. */}
        <Select
          value={current.currency}
          disabled={!owner || change.isPending}
          onValueChange={(value) => {
            if (value !== current.currency) change.mutate({ currency: value });
          }}
        >
          <SelectTrigger className="w-28" aria-label={words.currency}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {[...new Set([current.currency, ...(currencies.data ?? [])])].map((currency) => (
              <SelectItem key={currency} value={currency}>
                {currency}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Row>
      <Row label={words.limit} hint={words.limitHint}>
        <form
          className="flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (valid) change.mutate({ approval_limit: typed });
          }}
        >
          <Input
            inputMode="decimal"
            value={typed}
            onChange={(event) => setLimit(event.target.value)}
            disabled={!owner || change.isPending}
            aria-label={words.limit}
            aria-invalid={!valid}
            className="w-32 text-end tabular-nums"
          />
          <span className="text-muted-foreground text-sm">{current.currency}</span>
          {limit !== null ? (
            <Button type="submit" size="sm" disabled={!owner || !valid || change.isPending}>
              {t.settings.payments.save}
            </Button>
          ) : null}
        </form>
      </Row>
      <div className="border-border grid gap-2 border-t py-3">
        <span className="text-sm">
          {words.reserve}
          <span className="text-muted-foreground block text-xs text-pretty">{words.reserveHint}</span>
        </span>
        {editing ? (
          <ReserveEditor
            // A new draft for each rule opened: never one rule's fields saved under another's key.
            key={editing.rule.key}
            initial={editing.rule}
            putInUse={editing.copy}
            currency={current.currency}
            pending={change.isPending}
            onCancel={() => setEditing(null)}
            onSave={save}
          />
        ) : null}
        <ReserveChoices
          presets={presets.data ?? []}
          own={own}
          inUse={current.reserve.key}
          currency={current.currency}
          disabled={!owner || change.isPending}
          onUse={(key) => change.mutate({ reserve_rule: key })}
          onEdit={(rule, copy) => {
            const name = rule.key.startsWith("own:") ? rule.name : (words.presets[rule.key]?.name ?? rule.name);
            setEditing(copy ? { rule: copyOf(rule, words.copyName(name)), copy } : { rule, copy });
          }}
          onRemove={(key) => change.mutate({ remove_rule: key })}
        />
      </div>
      <PayDayField
        value={current.payment_weekday}
        disabled={!owner || change.isPending}
        onChange={(day) => change.mutate({ payment_weekday: day ?? -1 })}
      />
      {me.data && !owner ? <span className="text-muted-foreground text-xs">{t.settings.ownerOnly}</span> : null}
      {change.isError ? (
        <span className="text-danger-foreground text-xs" role="alert">
          {t.errors.generic}
        </span>
      ) : null}
    </section>
  );
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
      <h3 className="pb-2 text-base font-medium">{words.title}</h3>
      <Row label={words.recorded} hint={words.recordedHint}>
        <Segmented
          options={RECORDED.map((value) => ({ value, label: words[value] }))}
          value={policy.data.payments_recorded}
          label={words.recorded}
          disabled={!owner || change.isPending}
          onChange={(chosen) => {
            if (chosen !== policy.data?.payments_recorded) change.mutate({ payments_recorded: chosen });
          }}
        />
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


/** Rendered only while the dialog is open, in the browser; a browser with no notifications has
 * nothing to show here. */
function NotificationsSection() {
  const { t } = useI18n();
  if (!canNotify()) return null;
  return (
    <section className="flex flex-col">
      <h3 className="pb-2 text-base font-medium">{t.settings.notifications}</h3>
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
    <Segmented
      options={themes.map((value) => ({
        value,
        className: "gap-1.5",
        label: (
          <>
            <HugeiconsIcon icon={themeIcons[value]} strokeWidth={1.75} />
            {t.settings.themes[value]}
          </>
        ),
      }))}
      value={theme}
      label={t.settings.theme}
      onChange={(value) => {
        const next = readTheme(value);
        setTheme(next);
        applyTheme(next);
      }}
    />
  );
}

function LanguagePicker() {
  const { t, locale } = useI18n();
  const router = useRouter();

  return (
    <Segmented
      options={locales.map((value) => ({ value, label: localeNames[value], lang: value }))}
      value={locale}
      label={t.settings.language}
      onChange={(value) => {
        if (!isLocale(value) || value === locale) return;
        document.cookie = `${LOCALE_COOKIE}=${value}; path=/; max-age=31536000; samesite=lax`;
        // The server resolves the language, so it renders the app again in the new one.
        router.refresh();
      }}
    />
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
      <Segmented
        options={locales.map((value) => ({ value, label: localeNames[value], lang: value }))}
        value={me.data?.company.language ?? "en"}
        label={t.settings.writesIn}
        disabled={!owner || change.isPending}
        onChange={(value) => {
          if (isLocale(value) && value !== me.data?.company.language) change.mutate(value);
        }}
      />
      {me.data && !owner ? <span className="text-muted-foreground text-xs">{t.settings.ownerOnly}</span> : null}
      {change.isError ? (
        <span className="text-danger-foreground text-xs" role="alert">
          {t.errors.generic}
        </span>
      ) : null}
    </div>
  );
}
