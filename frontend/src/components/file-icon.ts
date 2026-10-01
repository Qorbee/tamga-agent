import {
  Doc01Icon,
  File01Icon,
  FileZipIcon,
  Globe02Icon,
  Image01Icon,
  Pdf01Icon,
  Txt01Icon,
  Xls01Icon,
} from "@hugeicons/core-free-icons";
import type { IconSvgElement } from "@hugeicons/react";

import type { Preview } from "@/lib/api";

const ICONS: Record<Preview, IconSvgElement> = {
  image: Image01Icon,
  pdf: Pdf01Icon,
  sheet: Xls01Icon,
  archive: FileZipIcon,
  office: Doc01Icon,
  html: Globe02Icon,
  text: Txt01Icon,
};

/** A file's icon by how Tamga shows it; a plain file for one that is only downloaded. */
export function fileIcon(preview: Preview | null | undefined): IconSvgElement {
  return preview ? ICONS[preview] : File01Icon;
}
