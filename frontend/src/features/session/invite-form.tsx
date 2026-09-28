"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState, useSyncExternalStore, type FormEvent } from "react";

import { Field } from "@/components/field";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { acceptInvite, previewInvite } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

import { authError } from "./errors";

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
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {authError(preview.error, t)}
      </p>
    );

  const invite = preview.data;
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    accept.mutate({ token, password, name: name.trim() || null });
  };

  return (
    <form onSubmit={onSubmit} className="grid gap-4">
      <div>
        <h1 className="text-xl font-medium">{t.auth.inviteTitle(invite.company_name)}</h1>
        <p className="text-muted-foreground mt-1 text-sm">
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
          />
        </Field>
      )}
      <Field
        label={invite.has_account ? t.auth.password : t.auth.newPassword}
        htmlFor="password"
        hint={invite.has_account ? undefined : t.auth.passwordHint}
      >
        <Input
          id="password"
          type="password"
          autoComplete={invite.has_account ? "current-password" : "new-password"}
          minLength={10}
          maxLength={256}
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </Field>
      {accept.isError ? (
        <p className="text-danger-foreground text-sm" role="alert">
          {authError(accept.error, t)}
        </p>
      ) : null}
      <Button type="submit" size="lg" disabled={accept.isPending}>
        {t.auth.accept}
      </Button>
    </form>
  );
}
