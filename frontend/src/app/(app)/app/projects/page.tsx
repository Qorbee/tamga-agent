import type { Metadata } from "next";

import { PageHeader } from "@/components/page-header";
import { ProjectBoard } from "@/features/projects/project-board";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).nav.projects };
}

export default async function ProjectsPage() {
  const t = getDictionary(await getLocale());
  return (
    <>
      <PageHeader title={t.nav.projects} />
      <ProjectBoard />
    </>
  );
}
