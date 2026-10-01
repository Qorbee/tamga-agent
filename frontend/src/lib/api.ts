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
async function send(path: string, init?: RequestInit): Promise<Response> {
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
  return response;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await send(path, init);
  return (response.status === 204 ? undefined : await response.json()) as T;
}

/** A file's bytes, for a viewer to draw. */
async function requestBlob(path: string): Promise<Blob> {
  return (await send(path)).blob();
}

// One typed function per operation. The type is an alias, never a restated interface.
export type Health = Schemas["Health"];
export const getHealth = () => request<Health>("/health");

export type Me = Schemas["Me"];
/** The signed-in user and their company; a 401 `ApiError` when no one is signed in. */
export const getMe = () => request<Me>("/me");
export const signOut = () => request<void>("/sessions/current", { method: "DELETE" });

/** A query string from defined values; arrays repeat the key (`status=a&status=b`). */
function query(params: Record<string, string | number | boolean | readonly string[] | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) for (const item of value) search.append(key, item);
    else search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

// Sign-in (ADR-0004): each answers with the session cookie and who is now signed in.
export type SignIn = Schemas["SignIn"];
export const signIn = (body: SignIn) => request<Me>("/sessions", json("POST", body));
export const createGuestSession = () => request<Me>("/sessions/guest", { method: "POST" });
export type InvitePreview = Schemas["InvitePreview"];
export const previewInvite = (token: string) =>
  request<InvitePreview>("/invites/lookup", json("POST", { token }));
export type InviteAccept = Schemas["InviteAccept"];
export const acceptInvite = (body: InviteAccept) =>
  request<Me>("/invites/accept", json("POST", body));

// Projects
export type Project = Schemas["ProjectOut"];
export type ProjectPage = Schemas["ProjectPage"];
export type ProjectStatus = Project["status"];
export type ProjectCreate = Schemas["ProjectCreate"];
export type ProjectUpdate = Schemas["ProjectUpdate"];
export const listProjects = (params: { status?: ProjectStatus[]; q?: string; limit?: number }) =>
  request<ProjectPage>(`/projects${query({ ...params, limit: params.limit ?? 200, sort: "-updated_at" })}`);
export const createProject = (body: ProjectCreate) =>
  request<Project>("/projects", json("POST", body));
export const updateProject = (id: string, body: ProjectUpdate) =>
  request<Project>(`/projects/${id}`, json("PATCH", body));

export type Shipment = Schemas["ShipmentOut"];
export const listShipments = (projectIds: readonly string[]) =>
  request<Shipment[]>(`/shipments${query({ project_id: projectIds })}`);

// Links and proposals: what waits on the owner.
export type ProjectLink = Schemas["LinkOut"];
export type LinkPage = Schemas["LinkPage"];
export type LinkCreate = Schemas["LinkCreate"];
export const listLinks = (params: { status?: ProjectLink["status"][]; limit?: number }) =>
  request<LinkPage>(`/project-links${query({ ...params, limit: params.limit ?? 50 })}`);
export const createLink = (body: LinkCreate) =>
  request<ProjectLink>("/project-links", json("POST", body));
export const updateLink = (id: string, body: Schemas["LinkUpdate"]) =>
  request<ProjectLink>(`/project-links/${id}`, json("PATCH", body));

export type FieldProposal = Schemas["ProposalOut"];
export type ProposalPage = Schemas["ProposalPage"];
export const listProposals = (params: { status?: FieldProposal["status"][]; limit?: number }) =>
  request<ProposalPage>(`/field-proposals${query({ ...params, limit: params.limit ?? 50 })}`);
export const decideProposal = (id: string, status: Schemas["ProposalDecision"]["status"]) =>
  request<FieldProposal>(`/field-proposals/${id}`, json("PATCH", { status }));

// Messages
export type MessageSummary = Schemas["MessageSummary"];
export type MessagePage = Schemas["MessagePage"];
export type Message = Schemas["MessageOut"];
export type Attachment = Schemas["AttachmentOut"];
export const listMessages = (params: { q?: string; thread_key?: string; limit?: number; offset?: number }) =>
  request<MessagePage>(`/messages${query({ ...params, limit: params.limit ?? 30 })}`);
export const getMessage = (id: string) => request<Message>(`/messages/${id}`);
/** The original mail (.eml). A link, not a fetch: the browser downloads it with the session cookie. */
export const rawMessageUrl = (id: string) => `${env.apiUrl}/messages/${id}/raw`;

// Conversations: mails sharing a thread key.
export type ThreadSummary = Schemas["ThreadSummary"];
export type ThreadPage = Schemas["ThreadPage"];
export const listThreads = (params: { q?: string; limit?: number; offset?: number }) =>
  request<ThreadPage>(`/threads${query({ ...params, limit: params.limit ?? 30 })}`);

// Documents
export type Document = Schemas["DocumentOut"];
export type DocumentDetail = Schemas["DocumentDetail"];
export type DocumentPage = Schemas["DocumentPage"];
export type DocumentType = Schemas["DocumentTypeOut"];
export type Upload = Schemas["UploadOut"];
export const listDocuments = (params: { q?: string; limit?: number }) =>
  request<DocumentPage>(`/documents${query({ ...params, limit: params.limit ?? 50 })}`);
export type Preview = NonNullable<Document["preview"]>;
export type Workbook = Schemas["WorkbookOut"];
export type Sheet = Schemas["SheetOut"];
export type SheetStyle = Schemas["CellStyleOut"];
export const getDocument = (id: string) => request<DocumentDetail>(`/documents/${id}`);
/** Drawable only as a verified image or PDF; anything else arrives as a download. */
export const getDocumentFile = (id: string) => requestBlob(`/documents/${id}/content`);
export const getDocumentSheets = (id: string) => request<Workbook>(`/documents/${id}/sheets`);
export const listDocumentTypes = () => request<DocumentType[]>("/document-types");
export const setDocumentType = (id: string, docType: string | null) =>
  request<Document>(`/documents/${id}`, json("PATCH", { doc_type: docType }));
/** The name Tamga shows it under; the backend refuses one that changes its extension. */
export const renameDocument = (id: string, filename: string) =>
  request<Document>(`/documents/${id}`, json("PATCH", { filename }));
/** Refused (409) for a file a mail brought or a project keeps: `held_by` says which. */
export const deleteDocument = (id: string) => request<void>(`/documents/${id}`, { method: "DELETE" });
export type Holder = DocumentDetail["held_by"][number];
/** The file itself is the body, typed by its own content type; the rest rides in the query. */
export const uploadDocument = (file: File, params: { docType?: string; note?: string }) =>
  request<Upload>(
    `/documents${query({ filename: file.name, doc_type: params.docType, note: params.note })}`,
    {
      method: "POST",
      body: file,
      headers: { "Content-Type": file.type || "application/octet-stream" },
    },
  );
