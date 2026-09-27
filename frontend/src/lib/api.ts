import type { components } from "./api-schema";
import { env } from "./env";

/** Every schema the backend declares, generated from backend/openapi.json. */
export type Schemas = components["schemas"];

/** A non-2xx response, carrying the backend's stable error `code` to branch on. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly field?: string;

  constructor(status: number, code: string, message: string, field?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.field = field;
  }
}

type ErrorBody = { error?: { code: string; message: string; field?: string } };

/** The only function that calls `fetch`; it speaks the backend's error envelope. */
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // The base URL is concatenated rather than resolved with `new URL(path, base)`, which would drop
  // a path prefix on the base (`https://host/api` + `/health` → `https://host/health`).
  const response = await fetch(`${env.apiUrl}${path}`, {
    ...init,
    // The session is an httpOnly cookie on the API's host (ADR-0004), so every call carries it.
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ErrorBody | null;
    throw new ApiError(
      response.status,
      body?.error?.code ?? "http_error",
      body?.error?.message ?? response.statusText,
      body?.error?.field,
    );
  }
  return (response.status === 204 ? undefined : await response.json()) as T;
}

// One typed function per operation. The type is an alias, never a restated interface.
export type Health = Schemas["Health"];
export const getHealth = () => request<Health>("/health");
