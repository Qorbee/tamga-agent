"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { Field } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { signIn } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

import { authError } from "./errors";

export function SignInForm() {
  const { t } = useI18n();
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const submit = useMutation({
    mutationFn: signIn,
    onSuccess: (me) => {
      queryClient.clear();
      queryClient.setQueryData(["me"], me);
      router.push(next);
    },
  });

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    submit.mutate({ email, password });
  };

  return (
    <form onSubmit={onSubmit} className="grid gap-4">
      <Field label={t.auth.email} htmlFor="email">
        <Input
          id="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </Field>
      <Field label={t.auth.password} htmlFor="password">
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </Field>
      {submit.isError ? (
        <p className="text-danger-foreground text-sm" role="alert">
          {authError(submit.error, t)}
        </p>
      ) : null}
      <Button type="submit" size="lg" disabled={submit.isPending}>
        {submit.isPending ? t.auth.signingIn : t.auth.signIn}
      </Button>
    </form>
  );
}

/** Only a path inside the app: a `next` pointing at another site would make sign-in an open redirect. */
function safeNext(next: string | null): string {
  return next?.startsWith("/app") && !next.startsWith("//") ? next : "/app";
}
