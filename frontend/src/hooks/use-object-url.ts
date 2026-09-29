"use client";

import { useEffect, useState } from "react";

/** A `blob:` URL for a file's bytes while the component shows it, revoked when it stops. */
export function useObjectUrl(blob: Blob | undefined): string | undefined {
  const [entry, setEntry] = useState<{ blob: Blob; url: string } | undefined>(undefined);

  useEffect(() => {
    // The URL is a browser resource whose lifetime has to straddle commit, so allocating it,
    // revoking it and clearing the entry that names it all belong to the effect.
    if (!blob) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setEntry(undefined);
      return;
    }
    const url = URL.createObjectURL(blob);
    setEntry({ blob, url });
    return () => URL.revokeObjectURL(url);
  }, [blob]);

  return entry !== undefined && entry.blob === blob ? entry.url : undefined;
}
