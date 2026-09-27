function required(name: string, value: string | undefined): string {
  if (!value) throw new Error(`${name} is not set — copy frontend/.env.example to frontend/.env`);
  return value;
}

/**
 * The only reader of `process.env` (root `AGENTS.md`, contract 1).
 *
 * NEXT_PUBLIC_ values are inlined into browser JavaScript, and only when written literally —
 * `process.env[name]` is undefined in the browser — so each one is spelled out. Never a secret.
 */
export const env = {
  apiUrl: required("NEXT_PUBLIC_API_URL", process.env.NEXT_PUBLIC_API_URL),
} as const;
