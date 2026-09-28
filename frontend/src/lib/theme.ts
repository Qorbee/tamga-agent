/**
 * The owner's theme: the device's setting (the default), light, or black. The choice is a cookie the
 * server renders as `data-theme-preference`; the browser resolves it to `data-theme`, which the
 * tokens in globals.css follow, before the first paint.
 */
export const themes = ["system", "light", "dark"] as const;
export type Theme = (typeof themes)[number];

export const THEME_COOKIE = "tamga_theme";

export function readTheme(value: string | undefined): Theme {
  return (themes as readonly string[]).includes(value ?? "") ? (value as Theme) : "system";
}

const DARK_QUERY = "(prefers-color-scheme: dark)";

/**
 * Inlined in <head> by the root layout, before the page paints, so no choice flashes the wrong
 * theme; it then follows the device when "system" is chosen and its setting changes. Only this
 * script and `applyTheme` set `data-theme`: the server renders the choice alone, so a server
 * re-render (a language change refreshes the page) never takes the resolved theme away.
 */
export const THEME_SCRIPT = `(() => {
  const root = document.documentElement;
  const dark = matchMedia(${JSON.stringify(DARK_QUERY)});
  const apply = () => {
    const choice = root.dataset.themePreference;
    root.dataset.theme = choice === "dark" || (choice !== "light" && dark.matches) ? "dark" : "light";
  };
  apply();
  dark.addEventListener("change", apply);
})();`;

/**
 * Switches the theme at once and remembers it. Browser only. Transitions are held for the switch,
 * or every control with a colour transition would fade a beat behind the rest of the page.
 */
export function applyTheme(theme: Theme): void {
  document.cookie = `${THEME_COOKIE}=${theme}; path=/; max-age=31536000; samesite=lax`;
  const hold = document.createElement("style");
  hold.textContent = "*,*::before,*::after{transition:none!important}";
  document.head.appendChild(hold);

  const root = document.documentElement;
  root.dataset.themePreference = theme;
  root.dataset.theme =
    theme === "dark" || (theme === "system" && matchMedia(DARK_QUERY).matches) ? "dark" : "light";

  // Paint the new colours with transitions off, then let them back on.
  void getComputedStyle(document.body).color;
  requestAnimationFrame(() => requestAnimationFrame(() => hold.remove()));
}
