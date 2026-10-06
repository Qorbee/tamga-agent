"use client";

import { useQueryClient } from "@tanstack/react-query";

/** Everything a decision on a placement or a link changes: the lists it is in, the counts beside
 * them, and the checklists it fills. */
export function useInvalidatePlacements(): () => void {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["placements"] });
    void queryClient.invalidateQueries({ queryKey: ["projects"] });
    void queryClient.invalidateQueries({ queryKey: ["project"] });
    void queryClient.invalidateQueries({ queryKey: ["project-links"] });
    // A paper answered or moved can fill or empty an item of a checklist, and the plates its job
    // names.
    void queryClient.invalidateQueries({ queryKey: ["checklist-items"] });
    void queryClient.invalidateQueries({ queryKey: ["plates"] });
  };
}
