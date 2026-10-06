"use client";

import {
  Add01Icon,
  Camera01Icon,
  Cancel01Icon,
  ContractsIcon,
  FileVerifiedIcon,
  Invoice01Icon,
  Invoice02Icon,
  MoneyReceive01Icon,
  MoneySend01Icon,
  MoreHorizontalIcon,
  PackageDeliveredIcon,
  Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Hint } from "@/components/hint";
import { IconButton } from "@/components/icon-button";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  createShipment,
  deleteChecklistMark,
  hitchShipment,
  listChecklistItems,
  listShipments,
  setChecklistMark,
  splitShipment,
  swapShipment,
  updateShipment,
  type ChecklistItem,
  type Shipment,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { useCanEditProjects, useIsOwner } from "@/lib/use-role";
import { cn } from "@/lib/utils";

export const trucksKey = (id: string) => ["shipments", [id]] as const;
export const checklistKey = (id: string) => ["checklist-items", id] as const;

export function useTrucks(id: string) {
  return useQuery({ queryKey: trucksKey(id), queryFn: () => listShipments([id]) });
}

export function useChecklist(id: string) {
  return useQuery({ queryKey: checklistKey(id), queryFn: () => listChecklistItems([id]) });
}

/** A truck as the owner knows it: its plate, and its trailer's after it. */
export function plateOf(truck: Shipment): string {
  return [truck.truck_plate, truck.trailer_plate].filter(Boolean).join(" · ") || "—";
}

const ICONS: Record<string, IconSvgElement> = {
  client_order: ContractsIcon,
  client_invoice: Invoice02Icon,
  client_paid: MoneyReceive01Icon,
  subcontractor_paid: MoneySend01Icon,
  loading_photo: Camera01Icon,
  cmr: FileVerifiedIcon,
  proof_of_delivery: PackageDeliveredIcon,
  subcontractor_invoice: Invoice01Icon,
};

const STATE: Record<ChecklistItem["state"], string> = {
  done: "text-success-foreground",
  waiting: "text-warning-foreground",
  missing: "text-danger-foreground",
  not_needed: "text-muted-foreground opacity-50",
};

/**
 * The job's trucks, one row each, with what each still lacks; the job's own items above them. A
 * truck Tamga proposed waits for the owner's yes or no. Tapping a truck shows only what is on it
 * in the file below.
 */
export function TruckBoard({
  id,
  selected,
  onSelect,
}: {
  id: string;
  selected: string | null;
  onSelect: (truckId: string | null) => void;
}) {
  const { t } = useI18n();
  const canEdit = useCanEditProjects();
  const trucks = useTrucks(id);
  const checklist = useChecklist(id);

  if (trucks.isError || checklist.isError)
    return (
      <p className="text-danger-foreground text-sm" role="alert">
        {t.errors.load}
      </p>
    );
  if (!trucks.data || !checklist.data) return <Skeleton className="h-24 rounded-2xl" />;

  const live = trucks.data.filter((truck) => truck.state !== "rejected");
  const rejected = trucks.data.length - live.length;
  const of = (truckId: string | null) => checklist.data.filter((item) => item.shipment_id === truckId);

  return (
    <section className="grid grid-cols-1 gap-2">
      <h4 className="flex items-baseline gap-2 text-sm font-medium">
        {t.project.trucks}
        <span className="text-muted-foreground tabular-nums">{live.length}</span>
      </h4>
      <ul className="divide-border border-border divide-y overflow-hidden rounded-2xl border">
        <li className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
          <span className="text-muted-foreground min-w-0 flex-1 text-sm">{t.project.job}</span>
          <ChecklistIcons items={of(null)} />
        </li>
        {live.length === 0 ? (
          <li className="text-muted-foreground px-4 py-3 text-sm">{t.project.noTrucks}</li>
        ) : (
          live.map((truck) => (
            <TruckRow
              key={truck.id}
              projectId={id}
              truck={truck}
              others={live.filter((other) => other.id !== truck.id)}
              items={of(truck.id)}
              selected={selected === truck.id}
              onSelect={() => onSelect(selected === truck.id ? null : truck.id)}
            />
          ))
        )}
      </ul>
      {canEdit ? <AddTruck projectId={id} /> : null}
      {rejected > 0 ? <p className="text-muted-foreground px-1 text-xs">{t.project.rejectedTrucks(rejected)}</p> : null}
    </section>
  );
}

function TruckRow({
  projectId,
  truck,
  others,
  items,
  selected,
  onSelect,
}: {
  projectId: string;
  truck: Shipment;
  // The job's other trucks not rejected: one may take this one's plate as its trailer.
  others: Shipment[];
  items: ChecklistItem[];
  selected: boolean;
  onSelect: () => void;
}) {
  const { t } = useI18n();
  const isOwner = useIsOwner();
  const canEdit = useCanEditProjects();
  const refresh = useRefreshTrucks(projectId);
  const [editing, setEditing] = useState(false);
  const answer = useMutation({
    mutationFn: (state: "confirmed" | "rejected") => updateShipment(truck.id, { state }),
    onSuccess: refresh,
  });
  // Refreshed whatever came of it: a step refused as stale shows the board as it is now.
  const change = useMutation({
    mutationFn: (step: () => Promise<unknown>) => step(),
    onSettled: refresh,
  });
  const plate = plateOf(truck);
  const proposed = truck.state === "proposed";
  const { truck_plate: pulls, trailer_plate: pulled } = truck;
  // A lone plate can be another truck's trailer, if that truck has none.
  const hitchable = pulls && !pulled ? others.filter((other) => other.truck_plate && !other.trailer_plate) : [];
  const failed = answer.error ?? change.error;

  return (
    <li className={cn("grid gap-1 py-1.5 ps-2 pe-2", selected && "bg-secondary")}>
      <div className="flex flex-wrap items-start gap-x-2 gap-y-1">
        <button
          type="button"
          aria-pressed={selected}
          aria-label={selected ? t.project.showAll : t.project.showTruck(plate)}
          className="hover:bg-accent/40 grid min-w-0 flex-1 basis-56 gap-1 rounded-xl px-2 py-1 text-start transition-colors"
          onClick={onSelect}
        >
          <Slot label={t.project.truckSlot} plate={pulls} read={readFrom(truck, "truck_plate")} />
          <Slot
            label={t.project.trailerSlot}
            plate={pulled}
            read={readFrom(truck, "trailer_plate")}
            empty={t.project.noTrailer}
          />
          <span className="text-muted-foreground flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs">
            {proposed ? (
              <span className="bg-warning-soft text-warning-foreground rounded-full px-2 py-0.5 font-medium">
                {t.project.proposedTruck}
              </span>
            ) : truck.status ? (
              <span>{t.project.shipmentStatus[truck.status]}</span>
            ) : null}
            {truck.driver_name ? <span className="truncate">{truck.driver_name}</span> : null}
          </span>
        </button>
        <span className="ms-auto flex shrink-0 items-center gap-0.5">
          <ChecklistIcons items={items} plate={plate} />
          {proposed && isOwner ? (
            <>
              <IconButton
                icon={Tick02Icon}
                label={t.project.confirmTruck}
                disabled={answer.isPending}
                onClick={() => answer.mutate("confirmed")}
              />
              <IconButton
                icon={Cancel01Icon}
                label={t.project.rejectTruck}
                className="text-muted-foreground"
                disabled={answer.isPending}
                onClick={() => answer.mutate("rejected")}
              />
            </>
          ) : null}
          {canEdit ? (
            <DropdownMenu modal={false}>
              <Hint label={t.project.truckActions}>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="text-muted-foreground"
                    aria-label={t.project.truckActions}
                    disabled={change.isPending}
                  >
                    <HugeiconsIcon icon={MoreHorizontalIcon} strokeWidth={1.75} />
                  </Button>
                </DropdownMenuTrigger>
              </Hint>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => setEditing(true)}>
                  {pulled || !pulls ? t.project.editPlates : t.project.addTrailer}
                </DropdownMenuItem>
                {pulls && pulled ? (
                  <>
                    <DropdownMenuItem
                      onSelect={() => change.mutate(() => swapShipment(truck.id))}
                    >
                      {t.project.swapPlates}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() => change.mutate(() => splitShipment(truck.id))}
                    >
                      {t.project.splitTruck}
                    </DropdownMenuItem>
                  </>
                ) : null}
                {isOwner && hitchable.length > 0 ? (
                  <DropdownMenuSub>
                    <DropdownMenuSubTrigger>{t.project.trailerOf}</DropdownMenuSubTrigger>
                    <DropdownMenuSubContent>
                      {hitchable.map((other) => (
                        <DropdownMenuItem
                          key={other.id}
                          className="font-mono"
                          // Its plate becomes the other's trailer, and this row no truck of the job.
                          onSelect={() =>
                            change.mutate(() => hitchShipment(other.id, { from_shipment_id: truck.id, plate: pulls ?? "" }))
                          }
                        >
                          {other.truck_plate}
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuSubContent>
                  </DropdownMenuSub>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
        </span>
      </div>
      {editing ? (
        <PlatesForm
          truckPlate={pulls ?? ""}
          trailerPlate={pulled ?? ""}
          pending={change.isPending}
          onCancel={() => setEditing(false)}
          onSave={(body) => {
            if (Object.keys(body).length === 0) return setEditing(false);
            change.mutate(() => updateShipment(truck.id, body), { onSuccess: () => setEditing(false) });
          }}
        />
      ) : null}
      {failed ? (
        <p className="text-danger-foreground px-2 text-xs" role="alert">
          {failed instanceof Error ? failed.message : t.errors.generic}
        </p>
      ) : null}
    </li>
  );
}

/** A labelled plate, the truck that pulls or the trailer it pulls, with where it was read. */
function Slot({ label, plate, read, empty }: { label: string; plate: string | null; read?: string; empty?: string }) {
  const { t } = useI18n();
  return (
    <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
      <span className="text-muted-foreground w-14 shrink-0 text-xs">{label}</span>
      {plate ? (
        <span className="truncate font-mono text-sm">{plate}</span>
      ) : (
        <span className="text-muted-foreground text-sm">{empty ?? "—"}</span>
      )}
      {plate && read ? (
        <span className="text-muted-foreground min-w-0 truncate text-xs">{t.project.readFrom(read)}</span>
      ) : null}
    </span>
  );
}

/** The item Tamga read one plate from, by name: the first of them, with how many more. */
function readFrom(truck: Shipment, field: "truck_plate" | "trailer_plate"): string | undefined {
  const labels = [
    ...new Set((truck.sources ?? []).filter((source) => source.field === field).map((source) => source.label)),
  ].filter((label): label is string => Boolean(label));
  if (labels.length === 0) return undefined;
  return labels.length > 1 ? `${labels[0]} +${labels.length - 1}` : labels[0];
}

/** The truck's and the trailer's plates typed in place. */
function PlatesForm({
  truckPlate,
  trailerPlate,
  pending,
  onSave,
  onCancel,
}: {
  truckPlate: string;
  trailerPlate: string;
  pending: boolean;
  // Only the plates typed in: one the owner did not touch is never sent back over a newer one.
  onSave: (changed: { truck_plate?: string | null; trailer_plate?: string | null }) => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  // What the form opened with: the board may refetch while it is open.
  const [opened] = useState({ truck: truckPlate, trailer: trailerPlate });
  const [pulls, setPulls] = useState(truckPlate);
  const [pulled, setPulled] = useState(trailerPlate);
  const field = (label: string, value: string, set: (value: string) => void, focus: boolean) => (
    <label className="grid min-w-0 flex-1 basis-36 gap-1">
      <span className="text-muted-foreground text-xs">{label}</span>
      <Input
        value={value}
        autoFocus={focus}
        onChange={(event) => set(event.target.value.toUpperCase())}
        placeholder={t.project.platePlaceholder}
        maxLength={20}
        disabled={pending}
        className="font-mono"
      />
    </label>
  );
  return (
    <form
      className="flex flex-wrap items-end gap-2 px-2 pb-1"
      onSubmit={(event) => {
        event.preventDefault();
        // Each plate sent is the owner's from then on.
        const changed: { truck_plate?: string | null; trailer_plate?: string | null } = {};
        if (pulls.trim() !== opened.truck) changed.truck_plate = pulls.trim() || null;
        if (pulled.trim() !== opened.trailer) changed.trailer_plate = pulled.trim() || null;
        onSave(changed);
      }}
    >
      {field(t.project.truckSlot, pulls, setPulls, !truckPlate)}
      {field(t.project.trailerSlot, pulled, setPulled, Boolean(truckPlate))}
      <span className="flex gap-1">
        <Button type="button" variant="ghost" size="sm" onClick={onCancel} disabled={pending}>
          {t.project.cancelPlates}
        </Button>
        <Button type="submit" size="sm" disabled={pending}>
          {t.project.savePlates}
        </Button>
      </span>
    </form>
  );
}

/** A truck the owner adds by its plates. */
function AddTruck({ projectId }: { projectId: string }) {
  const { t } = useI18n();
  const refresh = useRefreshTrucks(projectId);
  const [adding, setAdding] = useState(false);
  const add = useMutation({
    mutationFn: (plates: { truck_plate?: string | null; trailer_plate?: string | null }) =>
      createShipment({ project_id: projectId, ...plates }),
    onSuccess: () => {
      refresh();
      setAdding(false);
    },
  });
  if (!adding)
    return (
      <Button variant="ghost" size="sm" className="justify-self-start" onClick={() => setAdding(true)}>
        <HugeiconsIcon icon={Add01Icon} strokeWidth={1.75} />
        {t.project.addTruck}
      </Button>
    );
  return (
    <div className="border-border grid gap-1 rounded-2xl border py-2">
      <PlatesForm
        truckPlate=""
        trailerPlate=""
        pending={add.isPending}
        onCancel={() => setAdding(false)}
        onSave={(plates) => (plates.truck_plate || plates.trailer_plate ? add.mutate(plates) : setAdding(false))}
      />
      {add.isError ? (
        <p className="text-danger-foreground px-2 text-xs" role="alert">
          {add.error instanceof Error ? add.error.message : t.errors.generic}
        </p>
      ) : null}
    </div>
  );
}

/** What a change to a truck touches: the trucks, the checklist, and the papers code put on them. */
function useRefreshTrucks(projectId: string) {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["shipments"] });
    void queryClient.invalidateQueries({ queryKey: checklistKey(projectId) });
    void queryClient.invalidateQueries({ queryKey: ["project-links"] });
  };
}

/** One icon per item, coloured by where it stands; the owner marks one through its menu. */
function ChecklistIcons({ items, plate }: { items: ChecklistItem[]; plate?: string }) {
  return (
    <span className="flex shrink-0 items-center gap-0.5">
      {items.map((item) => (
        <ChecklistIcon key={item.key} item={item} plate={plate} />
      ))}
    </span>
  );
}

function ChecklistIcon({ item, plate }: { item: ChecklistItem; plate?: string }) {
  const { t } = useI18n();
  const isOwner = useIsOwner();
  const queryClient = useQueryClient();
  const name = t.project.checklist[item.key] ?? item.key;
  const label = `${name}: ${t.project.states[item.state]}`;
  const mark = useMutation({
    mutationFn: async (value: "not_needed" | "needed" | "done" | null) => {
      if (value === null) {
        if (item.mark) await deleteChecklistMark(item.mark.id);
        return;
      }
      await setChecklistMark({ project_id: item.project_id, shipment_id: item.shipment_id, key: item.key, mark: value });
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: checklistKey(item.project_id) }),
  });
  const icon = (
    <HugeiconsIcon icon={ICONS[item.key] ?? FileVerifiedIcon} strokeWidth={1.75} className="size-4" aria-hidden />
  );
  if (!isOwner)
    return (
      <Hint label={label}>
        <span className={cn("flex size-8 items-center justify-center", STATE[item.state])} aria-label={label} role="img">
          {icon}
        </span>
      </Hint>
    );
  return (
    <DropdownMenu modal={false}>
      <Hint label={label}>
        <DropdownMenuTrigger
          className={cn(
            "hover:bg-accent/40 flex size-8 items-center justify-center rounded-lg transition-colors",
            STATE[item.state],
          )}
          aria-label={label}
          disabled={mark.isPending}
        >
          {icon}
        </DropdownMenuTrigger>
      </Hint>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel className="text-muted-foreground text-xs font-normal">
          {plate ? `${name} · ${plate}` : name}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {item.required ? (
          <DropdownMenuItem onSelect={() => mark.mutate("not_needed")}>{t.project.marks.not_needed}</DropdownMenuItem>
        ) : (
          <DropdownMenuItem onSelect={() => mark.mutate("needed")}>{t.project.marks.needed}</DropdownMenuItem>
        )}
        {item.key !== "proof_of_delivery" && item.state !== "done" ? (
          <DropdownMenuItem onSelect={() => mark.mutate("done")}>{t.project.marks.done}</DropdownMenuItem>
        ) : null}
        {item.mark ? <DropdownMenuItem onSelect={() => mark.mutate(null)}>{t.project.marks.clear}</DropdownMenuItem> : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
