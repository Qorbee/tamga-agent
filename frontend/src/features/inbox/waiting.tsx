"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { OPEN_PROJECTS, ProjectCard, Questions } from "@/components/waiting-cards";
import { listAllPlacements, listProjects } from "@/lib/api";
import { useI18n } from "@/lib/i18n/client";

/** Project cards shown before "Show more". */
const CARDS = 20;
/** Projects one request for placements names: under the API's limit for a repeated parameter. */
const NAMED = 100;

/**
 * What waits for the owner, a card per project instead of a row per mail: the conversations Tamga
 * placed in it, each with its reason, answered one by one or all at once. Then the conversations
 * that fit more than one project, for the owner to say which. Shown only while something waits.
 */
export function Waiting() {
  const { t } = useI18n();
  const [limit, setLimit] = useState(CARDS);
  const projects = useQuery({
    queryKey: ["projects", { suggested: true, limit }],
    queryFn: () => listProjects({ status: [...OPEN_PROJECTS], suggested: true, limit }),
    placeholderData: keepPreviousData,
  });
  const ids = projects.data?.items.map((project) => project.id) ?? [];
  // Every placement of the cards shown, however many: a card's tick answers for all it lists.
  const placements = useQuery({
    queryKey: ["placements", { projects: ids }],
    queryFn: async () => {
      const groups = [];
      for (let start = 0; start < ids.length; start += NAMED)
        groups.push(listAllPlacements({ project_id: ids.slice(start, start + NAMED) }));
      return (await Promise.all(groups)).flat();
    },
    enabled: ids.length > 0,
    placeholderData: keepPreviousData,
  });
  const asked = useQuery({
    queryKey: ["placements", { outcome: "asked" }],
    queryFn: () => listAllPlacements({ outcome: ["asked"] }),
  });

  const failed = projects.isError || placements.isError || asked.isError;
  const cards = (projects.data?.items ?? [])
    .map((project) => ({
      project,
      waiting: (placements.data ?? []).filter(
        (placement) =>
          placement.project_id === project.id && placement.suggested_links > 0 && placement.conversation,
      ),
    }))
    // Only mail is suggested today; a project whose waiting links are of another kind has no row to answer.
    .filter((card) => card.waiting.length > 0);
  const questions = asked.data?.filter((placement) => placement.conversation) ?? [];
  // An empty section is noise above the mail.
  if (!failed && !projects.isPending && cards.length === 0 && questions.length === 0) return null;

  return (
    <section className="grid gap-3">
      <h2 className="text-sm font-medium">{t.inbox.waiting}</h2>
      {failed ? (
        <p className="text-danger-foreground text-sm" role="alert">
          {t.errors.load}
        </p>
      ) : projects.isPending || (ids.length > 0 && placements.isPending) ? (
        <Skeleton className="h-40 rounded-2xl" />
      ) : (
        <>
          {cards.map(({ project, waiting }) => (
            <ProjectCard key={project.id} project={project} waiting={waiting} />
          ))}
          {projects.data.total > projects.data.items.length ? (
            <Button
              variant="ghost"
              size="sm"
              className="justify-self-center"
              onClick={() => setLimit(limit + CARDS)}
            >
              {t.inbox.showMore}
            </Button>
          ) : null}
          {questions.length > 0 ? <Questions placements={questions} /> : null}
        </>
      )}
    </section>
  );
}
