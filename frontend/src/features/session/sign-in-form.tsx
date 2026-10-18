"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Field, fieldErrorId } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getMe, signIn } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

import { checkEmail, checkPassword } from "./checks";
import { authError } from "./errors";
import { FormAlert } from "./form-alert";
import { PasswordInput } from "./password-input";

// Taller than the app's fields: the one form a phone fills in with a thumb, often first.
export const AUTH_INPUT = "h-11 rounded-xl px-4 md:text-base";

/**
 * Email and password. The browser's own bubbles are off: a field is checked here when the form is
 * sent, its words said under it and its border marked, the first one wrong taking focus; once
 * sent, each is checked again as it is typed in, so a fixed field clears at once.
 */
export function SignInForm() {
  const { t } = useI18n();
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tried, setTried] = useState(false);
  const emailRef = useRef<HTMLInputElement>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const submit = useMutation({
    mutationFn: signIn,
    onSuccess: (me) => {
      queryClient.clear();
      queryClient.setQueryData(["me"], me);
      router.push(next);
    },
  });
  // Already signed in, such as a judge back at their sandbox: straight on into it. Asked afresh,
  // so a session that has just ended is not taken for one that holds.
  const me = useQuery({ queryKey: ["me"], queryFn: getMe, retry: false });
  const signedIn = me.isSuccess && me.isFetchedAfterMount;
  useEffect(() => {
    if (signedIn) router.replace(next);
  }, [signedIn, next, router]);
  const emailError = tried ? checkEmail(email, t) : null;
  const passwordError = tried ? checkPassword(password, t) : null;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    setTried(true);
    if (checkEmail(email, t)) return emailRef.current?.focus();
    if (checkPassword(password, t)) return passwordRef.current?.focus();
    submit.mutate({ email: email.trim(), password });
  };

  return (
    <form onSubmit={onSubmit} noValidate className="grid gap-4">
      <Field label={t.auth.email} htmlFor="email" error={emailError ?? undefined}>
        <Input
          ref={emailRef}
          id="email"
          type="email"
          inputMode="email"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          maxLength={320}
          aria-invalid={emailError ? true : undefined}
          aria-describedby={emailError ? fieldErrorId("email") : undefined}
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          className={AUTH_INPUT}
        />
      </Field>
      <Field label={t.auth.password} htmlFor="password" error={passwordError ?? undefined}>
        <PasswordInput
          ref={passwordRef}
          id="password"
          autoComplete="current-password"
          maxLength={256}
          aria-invalid={passwordError ? true : undefined}
          aria-describedby={passwordError ? fieldErrorId("password") : undefined}
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          className={AUTH_INPUT}
        />
      </Field>
      {submit.isError ? <FormAlert>{authError(submit.error, t)}</FormAlert> : null}
      <Button type="submit" size="lg" disabled={submit.isPending} className="mt-1 h-11 rounded-xl text-base">
        {submit.isPending ? t.auth.signingIn : t.auth.signIn}
      </Button>
    </form>
  );
}

/** Only a path inside the app: a `next` pointing at another site would make sign-in an open redirect. */
function safeNext(next: string | null): string {
  return next?.startsWith("/app") && !next.startsWith("//") ? next : "/app";
}
