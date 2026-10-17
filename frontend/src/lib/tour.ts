// The app's tour is the shell's; a page or the playground asks for it by this event, so no
// feature imports another (frontend AGENTS.md).
export const TOUR_EVENT = "tamga:tour";

export function startTour(): void {
  window.dispatchEvent(new Event(TOUR_EVENT));
}
