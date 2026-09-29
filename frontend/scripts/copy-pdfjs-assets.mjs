/**
 * Copies what pdf.js loads while it draws — character maps, the 14 standard fonts, the JBIG2 and
 * JPEG 2000 decoders, colour profiles — from the installed pdfjs-dist into public/pdfjs, so they
 * always match its version. Without them a scanned page draws white and some text not at all. Runs
 * before `dev` and `build`; public/pdfjs is not committed.
 */
import { cpSync, rmSync } from "node:fs";

const from = new URL("../node_modules/pdfjs-dist/", import.meta.url);
const to = new URL("../public/pdfjs/", import.meta.url);

rmSync(to, { recursive: true, force: true });
for (const folder of ["cmaps", "standard_fonts", "iccs"]) {
  cpSync(new URL(folder, from), new URL(folder, to), { recursive: true });
}
// The decoders only: quickjs runs a PDF's own scripts, which Tamga never does.
cpSync(new URL("wasm", from), new URL("wasm", to), {
  recursive: true,
  filter: (source) => !source.includes("quickjs"),
});
