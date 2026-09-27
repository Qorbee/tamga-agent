import { Folder01Icon } from "@hugeicons/core-free-icons";
import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export async function generateMetadata(): Promise<Metadata> {
  return { title: getDictionary(await getLocale()).nav.projects };
}

export default async function ProjectsPage() {
  const t = getDictionary(await getLocale());
  return (
    <>
      <header className="border-border border-b px-6 py-4 md:px-10">
        <h1 className="text-base font-medium">{t.nav.projects}</h1>
      </header>
      <EmptyState icon={Folder01Icon} title={t.empty.projects.title} body={t.empty.projects.body} />
    </>
  );
}
