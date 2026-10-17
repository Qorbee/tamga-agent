import { Page } from "@/components/page";
import { TranslateToggle } from "@/components/translated";
import { ProjectList } from "@/features/projects/project-list";
import { getDictionary } from "@/lib/i18n";
import { getLocale } from "@/lib/i18n/server";

export default async function ProjectsPage() {
  const t = getDictionary(await getLocale());
  return (
    <Page title={t.nav.projects} actions={<TranslateToggle />}>
      <ProjectList />
    </Page>
  );
}
