"use client";

import { useMutation } from "@tanstack/react-query";

import { getDocument } from "@/lib/api";

/**
 * Downloads a file under its own name. The backend signs the link for five minutes, so it is fetched
 * when the download is asked for, never kept from an earlier load. The link answers as an attachment:
 * following it saves the file and leaves the page as it is, and no popup blocker stands in the way
 * as it would for a window opened after a fetch.
 */
export function useDownload(id: string) {
  return useMutation({
    mutationFn: () => getDocument(id),
    onSuccess: (detail) => window.location.assign(detail.download_url),
  });
}
