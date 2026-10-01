"use client";

import { useQueryClient } from "@tanstack/react-query";

/** Everything a decision on a placement changes: the lists it is in and the counts beside them. */
export function useInvalidatePlacements(): () => void {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["placements"] });
    void queryClient.invalidateQueries({ queryKey: ["projects"] });
    void queryClient.invalidateQueries({ queryKey: ["project"] });
    void queryClient.invalidateQueries({ queryKey: ["project-links"] });
  };
}
