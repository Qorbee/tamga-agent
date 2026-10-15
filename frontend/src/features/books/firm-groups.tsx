"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Segmented } from "@/components/segmented";
import { Input } from "@/components/ui/input";
import { addPartyGroup, listPartyGroups, type PartyGroup, setGroupMember } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { useIsOwner } from "@/lib/use-role";

/**
 * The groups a firm is in ("needs cash", "key carriers"), for the company's own payment rules to
 * name (ADR-0033): each group a choice to turn on or off for this firm, and a field to start a new
 * one with it. A group is a label: it moves nothing until a rule names it.
 */
export function FirmGroups({ partyId }: { partyId: string }) {
  const { t } = useI18n();
  const words = t.books.groups;
  const owner = useIsOwner();
  const queryClient = useQueryClient();
  const groups = useQuery({ queryKey: ["party-groups"], queryFn: listPartyGroups });
  const [name, setName] = useState("");
  const done = () => void queryClient.invalidateQueries({ queryKey: ["party-groups"] });
  // One firm in or out at a time, so a change made elsewhere meanwhile is kept.
  const toggle = useMutation({
    mutationFn: (group: PartyGroup) => setGroupMember(group.id, partyId, !group.party_ids.includes(partyId)),
    onSuccess: done,
  });
  const add = useMutation({
    mutationFn: (named: string) => addPartyGroup({ name: named, party_ids: [partyId] }),
    onSuccess: () => {
      setName("");
      done();
    },
  });
  // Only the owner changes the groups: the company's own payment rules name them.
  const all = groups.data ?? [];
  const inside = all.filter((group) => group.party_ids.includes(partyId)).map((group) => group.id);

  return (
    <div className="grid gap-2">
      <span className="text-muted-foreground text-xs">{words.label}</span>
      {all.length > 0 ? (
        <Segmented
          multiple
          label={words.label}
          value={inside}
          disabled={!owner || toggle.isPending}
          onChange={(next) => {
            const changed = all.find((group) => next.includes(group.id) !== inside.includes(group.id));
            if (changed) toggle.mutate(changed);
          }}
          options={all.map((group) => ({ value: group.id, label: group.name }))}
          className="w-fit max-w-full"
        />
      ) : null}
      {owner ? (
        <form
          className="flex max-w-72 gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (name.trim()) add.mutate(name.trim());
          }}
        >
          <Input
            value={name}
            maxLength={60}
            placeholder={words.new}
            aria-label={words.new}
            onChange={(event) => setName(event.target.value)}
            disabled={add.isPending}
            className="h-8 text-sm"
          />
        </form>
      ) : null}
      {add.isError || toggle.isError ? (
        <span className="text-danger-foreground text-xs" role="alert">
          {t.errors.generic}
        </span>
      ) : null}
    </div>
  );
}
