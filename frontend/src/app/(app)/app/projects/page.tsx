import type { Metadata } from "next";

import { Page } from "@/components/page";
import { ProjectList } from "@/features/projects/project-list";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).nav.projects };
}

export default async function ProjectsPage() {
  const t = getDictionary(await getLocale());
  return (
    <Page title={t.nav.projects}>
      <ProjectList />
    </Page>
  );
}
