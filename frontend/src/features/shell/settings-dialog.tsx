"use client";

import { ComputerIcon, Moon02Icon, Sun03Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";

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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { MemorySection } from "@/components/memory";
import {
  copyOf,
  MethodsField,
  PayDayField,
  ReserveChoices,
  ReserveEditor,
  Row,
  SEGMENT,
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

/**
 * Settings, opened from the account menu, in tabs: how the app looks and reads on this device; the
 * company, and the language Tamga writes in for it; how it pays (the ways, the reserve, the pay
 * day) and how its books record payments; and what Tamga remembers.
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
        <Tabs defaultValue="general">
          <TabsList className="w-full">
            {(["general", "company", "payments", "memory"] as const).map((tab) => (
              <TabsTrigger key={tab} value={tab}>
                {t.settings.tabs[tab]}
              </TabsTrigger>
            ))}
          </TabsList>
          {/* Every tab stays mounted, hidden when not chosen: a draft typed in one survives a look
              at another. */}
          <TabsContent forceMount value="general" className="grid gap-4 data-[state=inactive]:hidden">
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
          </TabsContent>
          <TabsContent forceMount value="company" className="grid gap-4 data-[state=inactive]:hidden">
            <section className="flex flex-col">
              <h3 className="text-muted-foreground pb-1 text-xs font-medium">{t.settings.company}</h3>
              <Row label={t.settings.writesIn} hint={t.settings.writesInHint}>
                <WritingLanguagePicker />
              </Row>
            </section>
            <CompanyIdentitySection />
          </TabsContent>
          <TabsContent forceMount value="payments" className="grid gap-4 data-[state=inactive]:hidden">
            <HowYouPaySection />
            <PaymentsSection />
          </TabsContent>
          <TabsContent forceMount value="memory" className="data-[state=inactive]:hidden">
            <MemorySection />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
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
      <h3 className="text-muted-foreground pb-1 text-xs font-medium">{words.title}</h3>
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
