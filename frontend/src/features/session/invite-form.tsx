"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useRef, useState, useSyncExternalStore, type FormEvent } from "react";

import { Field, fieldErrorId } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { acceptInvite, previewInvite } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

import { checkPassword } from "./checks";
import { authError } from "./errors";
import { FormAlert } from "./form-alert";
import { PasswordInput } from "./password-input";
import { AUTH_INPUT } from "./sign-in-form";

// What the backend asks of a new password.
const NEW_PASSWORD_LEAST = 10;

/** The invite token rides in the URL's fragment, which the browser never sends to a server. */
function useInviteToken(): string | null {
  return useSyncExternalStore(
    (onChange) => {
      window.addEventListener("hashchange", onChange);
      return () => window.removeEventListener("hashchange", onChange);
    },
    () => window.location.hash.slice(1) || null,
    () => null,
  );
}

export function InviteForm() {
  const { t } = useI18n();
  const token = useInviteToken();
  const router = useRouter();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [tried, setTried] = useState(false);
  const passwordRef = useRef<HTMLInputElement>(null);
  const preview = useQuery({
    queryKey: ["invite", token],
    queryFn: () => previewInvite(token ?? ""),
    enabled: token !== null,
    retry: false,
  });
  const accept = useMutation({
    mutationFn: acceptInvite,
    onSuccess: (me) => {
      queryClient.clear();
      queryClient.setQueryData(["me"], me);
      // The token is spent; drop it from the address bar and history.
      window.history.replaceState(null, "", window.location.pathname);
      router.push("/app");
    },
  });

  if (token === null) return <p className="text-muted-foreground text-sm">{t.auth.inviteMissing}</p>;
  if (preview.isPending) return <Skeleton className="h-40 w-full rounded-2xl" />;
  if (preview.isError)
    return <FormAlert>{authError(preview.error, t)}</FormAlert>;

  const invite = preview.data;
  const least = invite.has_account ? 0 : NEW_PASSWORD_LEAST;
  const passwordError = tried ? checkPassword(password, t, least) : null;
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setTried(true);
    if (checkPassword(password, t, least)) return passwordRef.current?.focus();
    accept.mutate({ token, password, name: name.trim() || null });
  };

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-4">
      <div className="mb-4 grid gap-2">
        <h1 className="text-3xl font-medium tracking-tight text-balance">{t.auth.inviteTitle(invite.company_name)}</h1>
        <p className="text-muted-foreground text-pretty">
          {invite.has_account ? t.auth.inviteExisting : t.auth.inviteBody(invite.email)}
        </p>
      </div>
      {invite.has_account ? null : (
        <Field label={t.auth.name} htmlFor="name">
          <Input
            id="name"
            autoComplete="name"
            maxLength={200}
            value={name}
            onChange={(event) => setName(event.target.value)}
            className={AUTH_INPUT}
          />
        </Field>
      )}
      <Field
        label={invite.has_account ? t.auth.password : t.auth.newPassword}
        htmlFor="password"
        hint={invite.has_account ? undefined : t.auth.passwordHint}
        error={passwordError ?? undefined}
      >
        <PasswordInput
          ref={passwordRef}
          id="password"
          autoComplete={invite.has_account ? "current-password" : "new-password"}
          maxLength={256}
          aria-invalid={passwordError ? true : undefined}
          aria-describedby={passwordError ? fieldErrorId("password") : undefined}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className={AUTH_INPUT}
        />
      </Field>
      {accept.isError ? <FormAlert>{authError(accept.error, t)}</FormAlert> : null}
      <Button type="submit" size="lg" disabled={accept.isPending} className="mt-1 h-11 rounded-xl text-base">
        {t.auth.accept}
      </Button>
    </form>
  );
}
