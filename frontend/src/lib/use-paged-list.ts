"use client";

import { keepPreviousData, useInfiniteQuery, type QueryKey } from "@tanstack/react-query";

type Page<T> = { items: T[]; total: number };

/**
 * A list the API serves a page at a time, grown by "Show more": each step reads the page after
 * what is shown, so no request asks for more rows than the API serves at once (200). A row that
 * moved down a page while more were read is shown once. A next page that fails leaves the rows
 * shown, and "Show more" can be asked again.
 */
export function usePagedList<T>(
  queryKey: QueryKey,
  read: (page: { limit: number; offset: number }) => Promise<Page<T>>,
  size: number,
  keyOf: (item: T) => string,
) {
  const query = useInfiniteQuery({
    queryKey,
    queryFn: ({ pageParam }) => read({ limit: size, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const shown = pages.reduce((count, page) => count + page.items.length, 0);
      return shown < last.total ? shown : undefined;
    },
    placeholderData: keepPreviousData,
  });
  const seen = new Set<string>();
  const items = query.data?.pages
    .flatMap((page) => page.items)
    .filter((item) => !seen.has(keyOf(item)) && Boolean(seen.add(keyOf(item))));
  return {
    items,
    /** Nothing could be read: rows already shown stay when a later page fails. */
    isError: query.isError && items === undefined,
    isPending: query.isPending,
    /** The rows of the query before the key changed, kept while the new one is read. */
    isPlaceholderData: query.isPlaceholderData,
    /** Reads the next page; null when every row is shown. */
    more: query.hasNextPage ? () => void query.fetchNextPage() : null,
    // Also while the shown pages are read again: asking for more then would cancel that.
    loadingMore: query.isFetching,
  };
}
