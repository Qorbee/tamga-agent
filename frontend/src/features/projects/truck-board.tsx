"use client";

import {
  Camera01Icon,
  Cancel01Icon,
  ContractsIcon,
  FileVerifiedIcon,
  Invoice01Icon,
  Invoice02Icon,
  MoneyReceive01Icon,
  MoneySend01Icon,
  PackageDeliveredIcon,
  Tick02Icon,
} from "@hugeicons/core-free-icons";
import { HugeiconsIcon, type IconSvgElement } from "@hugeicons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Hint } from "@/components/hint";
import { IconButton } from "@/components/icon-button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  deleteChecklistMark,
  listChecklistItems,
  listShipments,
  setChecklistMark,
  updateShipment,
  type ChecklistItem,
  type Shipment,
} from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";
import { useIsOwner } from "@/lib/use-role";
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
              items={of(truck.id)}
              selected={selected === truck.id}
              onSelect={() => onSelect(selected === truck.id ? null : truck.id)}
            />
          ))
        )}
      </ul>
      {rejected > 0 ? <p className="text-muted-foreground px-1 text-xs">{t.project.rejectedTrucks(rejected)}</p> : null}
    </section>
  );
}

function TruckRow({
  projectId,
  truck,
  items,
  selected,
  onSelect,
}: {
  projectId: string;
  truck: Shipment;
  items: ChecklistItem[];
  selected: boolean;
  onSelect: () => void;
}) {
  const { t } = useI18n();
  const isOwner = useIsOwner();
  const queryClient = useQueryClient();
  const answer = useMutation({
    mutationFn: (state: "confirmed" | "rejected") => updateShipment(truck.id, { state }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["shipments"] });
      void queryClient.invalidateQueries({ queryKey: checklistKey(projectId) });
      // A rejected truck loses the papers Tamga put on it.
      void queryClient.invalidateQueries({ queryKey: ["project-links"] });
    },
  });
  const plate = plateOf(truck);
  const proposed = truck.state === "proposed";

  return (
    <li className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 ps-2 pe-3", selected && "bg-secondary")}>
      <button
        type="button"
        aria-pressed={selected}
        aria-label={selected ? t.project.showAll : t.project.showTruck(plate)}
        className="hover:bg-accent/40 grid min-w-0 flex-1 gap-0.5 rounded-xl px-2 py-1 text-start transition-colors"
        onClick={onSelect}
      >
        <span className="truncate font-mono text-sm">{plate}</span>
        <span className="text-muted-foreground flex flex-wrap gap-x-2 text-xs">
          {proposed ? <span className="text-warning-foreground">{t.project.proposedTruck}</span> : null}
          {truck.driver_name ? <span className="truncate">{truck.driver_name}</span> : null}
          {truck.status ? <span>{t.project.shipmentStatus[truck.status]}</span> : null}
        </span>
      </button>
      <ChecklistIcons items={items} plate={plate} />
      {proposed && isOwner ? (
        <span className="flex shrink-0 items-center gap-0.5">
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
        </span>
      ) : null}
      {answer.isError ? (
        <p className="text-danger-foreground w-full px-2 text-xs" role="alert">
          {answer.error instanceof Error ? answer.error.message : t.errors.generic}
        </p>
      ) : null}
    </li>
  );
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
