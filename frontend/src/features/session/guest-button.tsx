"use client";

import { useState, type ComponentProps } from "react";

import { ScenarioPicker } from "@/components/scenario-picker";
import { Button } from "@/components/ui/button";

/** The judge's way in (ADR-0004): pick a scenario, and a private sandbox opens with it. */
export function GuestButton({ label, ...props }: { label: string } & ComponentProps<typeof Button>) {
  const [picking, setPicking] = useState(false);
  return (
    <>
      <Button {...props} onClick={() => setPicking(true)}>
        {label}
      </Button>
      <ScenarioPicker open={picking} onOpenChange={setPicking} />
    </>
  );
}
