import type { components, operations } from "./api-schema";
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
/** The language Tamga writes in for the company: the names it gives projects, its reasons. */
export type WritingLanguage = Me["company"]["language"];

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

/** The most a list endpoint gives at once. */
const PAGE = 200;

// Sign-in (ADR-0004): each answers with the session cookie and who is now signed in.
export type SignIn = Schemas["SignIn"];
export const signIn = (body: SignIn) => request<Me>("/sessions", json("POST", body));
/** A judge's sandbox, opened with the scenario they picked (the default when none). */
export type GuestStart = Schemas["GuestStart"];
export const createGuestSession = (body: GuestStart) => request<Me>("/sessions/guest", json("POST", body));
export type InvitePreview = Schemas["InvitePreview"];
export const previewInvite = (token: string) =>
  request<InvitePreview>("/invites/lookup", json("POST", { token }));
export type InviteAccept = Schemas["InviteAccept"];
export const acceptInvite = (body: InviteAccept) =>
  request<Me>("/invites/accept", json("POST", body));

/** What Tamga wrote, in another language; a text that could not be translated comes back as it was. */
export const translate = (body: Schemas["TranslationRequest"]) =>
  request<Schemas["TranslationOut"]>("/translations", json("POST", body));

export const updateCompany = (body: Schemas["CompanyUpdate"]) =>
  request<Me>("/me/company", json("PATCH", body));

export type CompanyProfile = Schemas["CompanyProfile"];
export type IdentityChange = Schemas["IdentityChange"];
export type IdentityCheck = Schemas["IdentityCheck"];
export type CheckFinding = Schemas["CheckFinding"];
export type OwnDomain = Schemas["OwnDomain"];
/** Who the company is: its legal name, tax number and mail domains, each with what shows it. */
export const getCompanyProfile = () => request<CompanyProfile>("/company/profile");
/** Saved only when the checks find nothing against it, or the owner confirms their doubts. */
export const setCompanyTaxId = (body: Schemas["TaxIdSet"]) =>
  request<IdentityChange>("/company/profile/tax-id", json("PUT", body));
export const addCompanyDomain = (body: Schemas["DomainAdd"]) =>
  request<IdentityChange>("/company/profile/domains", json("POST", body));
/** The owner says a domain is not the company's. */
export const removeCompanyDomain = (domain: string) =>
  request<CompanyProfile>(`/company/profile/domains/${encodeURIComponent(domain)}`, { method: "DELETE" });

// Projects
export type Project = Schemas["ProjectOut"];
export type ProjectPage = Schemas["ProjectPage"];
export type ProjectStatus = Project["status"];
export type ProjectCreate = Schemas["ProjectCreate"];
export type ProjectUpdate = Schemas["ProjectUpdate"];
export const listProjects = (params: {
  status?: ProjectStatus[];
  q?: string;
  /** Only projects holding a link that waits for the owner. */
  suggested?: boolean;
  limit?: number;
  offset?: number;
}) => request<ProjectPage>(`/projects${query({ ...params, limit: params.limit ?? PAGE, sort: "-updated_at" })}`);
export const getProject = (id: string) => request<Project>(`/projects/${id}`);
export const createProject = (body: ProjectCreate) =>
  request<Project>("/projects", json("POST", body));
export const updateProject = (id: string, body: ProjectUpdate) =>
  request<Project>(`/projects/${id}`, json("PATCH", body));
/** This project goes into another, which is returned: one job that was held as two. */
export const mergeProject = (id: string, body: Schemas["ProjectMerge"]) =>
  request<Project>(`/projects/${id}/merge`, json("POST", body));

// A project's parties: the owner's, which stand over what was read of the job, and the firms
// Tamga suggests from what names them, which the owner answers.
export type ProjectParty = Schemas["ProjectPartyOut"];
export type ProjectRole = ProjectParty["role"];
export const listProjectParties = (projectId: string) =>
  request<ProjectParty[]>(`/project-parties${query({ project_id: projectId })}`);
export const addProjectParty = (body: Schemas["ProjectPartyCreate"]) =>
  request<ProjectParty>("/project-parties", json("POST", body));
export const removeProjectParty = (id: string) => request<void>(`/project-parties/${id}`, { method: "DELETE" });
export const answerProjectParty = (id: string, body: Schemas["ProjectPartyAnswer"]) =>
  request<ProjectParty>(`/project-parties/${id}`, json("PATCH", body));

// Parties: everyone the company deals with, to choose a project's firms from.
export type Party = Schemas["PartyOut"];
export type PartyPage = Schemas["PartyPage"];
type PartyQuery = NonNullable<operations["parties.list_parties"]["parameters"]["query"]>;
export type PartyChange = Schemas["PartyChange"];
export const changeParty = (id: string, body: PartyChange) => request<Party>(`/parties/${id}`, json("PATCH", body));
export const listParties = (params: PartyQuery) =>
  request<PartyPage>(`/parties${query({ ...params, limit: params.limit ?? 20 })}`);
export const getParty = (id: string) => request<Party>(`/parties/${id}`);

export type Shipment = Schemas["ShipmentOut"];
export const listShipments = (projectIds: readonly string[]) =>
  request<Shipment[]>(`/shipments${query({ project_id: projectIds })}`);
/** A truck the owner adds: theirs, confirmed, with the plates typed. */
export const createShipment = (body: Schemas["ShipmentCreate"]) =>
  request<Shipment>("/shipments", json("POST", body));
/** The owner's edit of a truck, and their yes or no to one Tamga proposed (`state`). */
export const updateShipment = (id: string, body: Schemas["ShipmentUpdate"]) =>
  request<Shipment>(`/shipments/${id}`, json("PATCH", body));
/** The plates these projects' mail, papers and bills name, with where each was read and its truck. */
export type PlateSeen = Schemas["PlateSeen"];
export type ItemNamed = Schemas["ItemNamed"];
export const listPlates = (projectIds: readonly string[]) =>
  request<PlateSeen[]>(`/plates${query({ project_id: projectIds })}`);

/** The truck's trailer made a truck of its own, in one step; returns the new truck. */
export const splitShipment = (id: string) => request<Shipment>(`/shipments/${id}/split`, { method: "POST" });
/** The truck's plates the other way round, as they stand on the server. */
export const swapShipment = (id: string) => request<Shipment>(`/shipments/${id}/swap`, { method: "POST" });
/** Another truck's lone plate, or a plate seen on the job, made this truck's trailer; 409 when it changed. */
export const hitchShipment = (id: string, body: Schemas["ShipmentHitch"]) =>
  request<Shipment>(`/shipments/${id}/hitch`, json("POST", body));

// What a job and each of its trucks still lack, and the owner's marks over it.
export type ChecklistItem = Schemas["ChecklistItemOut"];
export type ChecklistMark = Schemas["ChecklistMarkOut"];
export const listChecklistItems = (projectIds: readonly string[]) =>
  request<ChecklistItem[]>(`/checklist-items${query({ project_id: projectIds })}`);
/** A second mark on the same item replaces the first. */
export const setChecklistMark = (body: Schemas["ChecklistMarkSet"]) =>
  request<ChecklistMark>("/checklist-marks", json("PUT", body));
export const deleteChecklistMark = (id: string) => request<void>(`/checklist-marks/${id}`, { method: "DELETE" });

// Links and proposals: what waits on the owner.
export type ProjectLink = Schemas["LinkOut"];
export type LinkPage = Schemas["LinkPage"];
export type LinkCreate = Schemas["LinkCreate"];
/** What a link ties, with when and how it came: given when the list is asked to `expand` it. */
export type LinkItem = NonNullable<ProjectLink["item"]>;
type LinkFilter = {
  project_id?: string;
  shipment_id?: string;
  item_kind?: ProjectLink["item_kind"];
  status?: ProjectLink["status"][];
  expand?: "item"[];
};
export const listLinks = (params: LinkFilter & { limit?: number; offset?: number }) =>
  request<LinkPage>(`/project-links${query({ ...params, limit: params.limit ?? PAGE })}`);
/** Every page of them: a project's whole file, not its first page. */
export async function listAllLinks(params: LinkFilter): Promise<ProjectLink[]> {
  const items: ProjectLink[] = [];
  for (;;) {
    const page = await listLinks({ ...params, offset: items.length });
    items.push(...page.items);
    if (page.items.length === 0 || items.length >= page.total) return items;
  }
}
export const createLink = (body: LinkCreate) =>
  request<ProjectLink>("/project-links", json("POST", body));
/** The owner's answer on one item: confirmed or rejected, its truck, its proof. */
export const updateLink = (id: string, body: Schemas["LinkUpdate"]) =>
  request<ProjectLink>(`/project-links/${id}`, json("PATCH", body));

// Placements: where the linker put each conversation, and why.
export type Placement = Schemas["PlacementOut"];
export type PlacementPage = Schemas["PlacementPage"];
type PlacementFilter = { outcome?: NonNullable<Placement["outcome"]>[]; project_id?: readonly string[] };
export const listPlacements = (params: PlacementFilter & { limit?: number; offset?: number }) =>
  request<PlacementPage>(`/placements${query({ ...params, limit: params.limit ?? PAGE })}`);
/** Every page of them: what answers for "all of these" must hold all of them. */
export async function listAllPlacements(params: PlacementFilter): Promise<Placement[]> {
  const items: Placement[] = [];
  for (;;) {
    const page = await listPlacements({ ...params, offset: items.length });
    items.push(...page.items);
    if (page.items.length === 0 || items.length >= page.total) return items;
  }
}
/** The owner's answer for a whole conversation: its waiting links, or the project it belongs to. */
export const getPlacement = (id: string) => request<Placement>(`/placements/${id}`);
export const decidePlacement = (id: string, body: Schemas["PlacementDecision"]) =>
  request<Placement>(`/placements/${id}`, json("PATCH", body));

export type FieldProposal = Schemas["ProposalOut"];
export type ProposalPage = Schemas["ProposalPage"];
export const listProposals = (params: { status?: FieldProposal["status"][]; limit?: number }) =>
  request<ProposalPage>(`/field-proposals${query({ ...params, limit: params.limit ?? 50 })}`);
export const decideProposal = (id: string, status: Schemas["ProposalDecision"]["status"]) =>
  request<FieldProposal>(`/field-proposals/${id}`, json("PATCH", { status }));

// The company event stream: a note that something changed, never its content. What changed is
// read again through the resources above.
export type CompanyEvent = Schemas["CompanyEvent"];
export const openCompanyEvents = () =>
  new EventSource(`${env.apiUrl}/events`, { withCredentials: true });

// Books: invoices as the accounting system holds them, read only.
export type Invoice = Schemas["InvoiceOut"];
export type Policy = Schemas["PolicyOut"];
/** The company's policy in force; version 0 is the defaults, while the owner has set none. */
export const getPolicy = () => request<Policy>("/policy");
/** A new version of the policy with only these fields changed from the one in force: the owner's
 * only. */
export const changePolicy = (body: Schemas["PolicyChange-Input"]) =>
  request<Policy>("/policy", json("PATCH", body));
export type PolicyChange = Schemas["PolicyChange-Input"];
/** A payment rule of the company's own (ADR-0033), and a group of firms one may name. */
export type CustomRule = Schemas["CustomRule-Output"];
export type PartyGroup = Schemas["PartyGroupOut"];
/** Every group of the company's: a company names a few, so one page of the most there is. */
export const listPartyGroups = async () =>
  (await request<Schemas["PartyGroupPage"]>("/party-groups?limit=100")).items;
/** One firm put in a group, or taken out, leaving its other members as they are now. */
export const setGroupMember = (groupId: string, partyId: string, inside: boolean) =>
  request<PartyGroup>(`/party-groups/${groupId}/members/${partyId}`, { method: inside ? "PUT" : "DELETE" });
export const addPartyGroup = (body: Schemas["PartyGroupIn"]) => request<PartyGroup>("/party-groups", json("POST", body));
export const savePartyGroup = (id: string, body: Schemas["PartyGroupIn"]) =>
  request<PartyGroup>(`/party-groups/${id}`, json("PUT", body));
/** The open bills a rule of the company's own applies to now; nothing changes. */
export const decisionsMatching = (rule: Schemas["CustomRule-Input"]) =>
  request<Decision[]>("/decisions/matching", json("POST", rule));
export type PolicyDraft = Schemas["PolicyDraftOut"];
/** A change to the policy drafted in a chat, and whether the owner answered it. */
export const getPolicyDraft = (id: string) => request<PolicyDraft>(`/policy/drafts/${id}`);
/** Puts the drafted change in force over the version in force now: the owner's only. */
export const confirmPolicyDraft = (id: string) => request<PolicyDraft>(`/policy/drafts/${id}/confirm`, json("POST", {}));
export const discardPolicyDraft = (id: string) => request<PolicyDraft>(`/policy/drafts/${id}/discard`, json("POST", {}));
export type PaymentMethod = Schemas["PaymentMethod"];
export type ReserveRule = Schemas["ReserveRule-Output"];
/** The currencies the company's official reference rates convert, the base first. */
export const getReferenceCurrencies = () => request<string[]>("/reference-currencies");
/** The reserve rules every company can pick, each a start for one of the owner's own. */
export const getReservePresets = () => request<ReserveRule[]>("/policy/reserve-presets");
export type InvoicePage = Schemas["InvoicePage"];
type InvoiceQuery = NonNullable<operations["invoices.list_invoices"]["parameters"]["query"]>;
export const listInvoices = (params: InvoiceQuery) => request<InvoicePage>(`/invoices${query(params)}`);
export type InvoiceTotal = Schemas["InvoiceTotal"];
type InvoiceTotalQuery = NonNullable<operations["invoices.invoice_totals"]["parameters"]["query"]>;
/** What the invoices the same filters list add up to, one figure per currency. */
export const listInvoiceTotals = (params: InvoiceTotalQuery) =>
  request<InvoiceTotal[]>(`/invoice-totals${query(params)}`);
export const getInvoice = (id: string) => request<Invoice>(`/invoices/${id}`);
export type InvoiceLine = Schemas["InvoiceLineOut"];
/** What these bills charge for, line by line, as the bill reader read their files. */
export const listInvoiceLines = (invoiceIds: readonly string[]) =>
  request<InvoiceLine[]>(`/invoice-lines${query({ invoice_id: invoiceIds.slice(0, PAGE) })}`);
export type InvoiceFile = Schemas["InvoiceFile"];
/** The invoice's own file: one its mail carried, or the one the books render (read once, kept). */
export const getInvoiceFile = (id: string) => request<InvoiceFile>(`/invoices/${id}/file`, { method: "POST" });

// The company's memory (ADR-0024): what the owner taught Tamga; only the owner makes it hold.
export type MemoryEntry = Schemas["MemoryEntryOut"];
export type MemoryPage = Schemas["MemoryPage"];
export type MemoryKind = Schemas["MemoryKind"];
export type MemoryChanges = Schemas["MemoryChanges"];
type MemoryQuery = NonNullable<operations["memory.list_memory"]["parameters"]["query"]>;
export const listMemory = (params: MemoryQuery) => request<MemoryPage>(`/memory${query(params)}`);
/** Every entry the filters list, page by page: the owner sees and retires each one. */
export async function listAllMemory(params: MemoryQuery): Promise<MemoryEntry[]> {
  const items: MemoryEntry[] = [];
  for (;;) {
    const page = await listMemory({ ...params, offset: items.length, limit: 200 });
    items.push(...page.items);
    if (page.items.length === 0 || items.length >= page.total) return items;
  }
}
export const getMemoryEntry = (id: string) => request<MemoryEntry>(`/memory/${id}`);
/** An entry the owner writes: it holds at once. */
export const writeMemoryEntry = (body: Schemas["MemoryIn"]) =>
  request<MemoryEntry>("/memory", json("POST", body));
/** What someone wrote, made into changes of the memory by a model that reads it first (ADR-0030). */
export const changeMemory = (body: Schemas["MemoryText"]) =>
  request<Schemas["MemoryChanges"]>("/memory/changes", json("POST", body));
/** The owner corrects, confirms (`held`) or retires an entry. */
export const changeMemoryEntry = (id: string, body: Schemas["MemoryChange"]) =>
  request<MemoryEntry>(`/memory/${id}`, json("PATCH", body));
/** The owner throws away a draft; one that held is retired instead. */
export const discardMemoryEntry = (id: string) => request<void>(`/memory/${id}`, { method: "DELETE" });

// Decisions and payments (docs/architecture/05)
export type Decision = Schemas["DecisionOut"];
export type DecisionPage = Schemas["DecisionPage"];
export type DecisionAction = Schemas["DecisionAction"];
type DecisionQuery = NonNullable<operations["decisions.list_decisions"]["parameters"]["query"]>;
export const listDecisions = (params: DecisionQuery) => request<DecisionPage>(`/decisions${query(params)}`);
export const getDecision = (id: string) => request<Decision>(`/decisions/${id}${query({ expand: ["invoice"] })}`);
/** The owner's answer to the evaluation it names; a 409 when it was evaluated again since. */
export const answerDecision = (id: string, body: DecisionAction) =>
  request<Decision>(`/decisions/${id}`, json("PATCH", body));
export type Payment = Schemas["PaymentOut"];
export type PaymentPage = Schemas["PaymentPage"];
type PaymentQuery = NonNullable<operations["payments.list_payments"]["parameters"]["query"]>;
export const listPayments = (params: PaymentQuery) => request<PaymentPage>(`/payments${query(params)}`);
export type DecisionEvent = Schemas["DecisionEventOut"];
export type DecisionEventPage = Schemas["DecisionEventPage"];
type DecisionEventQuery = NonNullable<operations["decisions.list_decision_events"]["parameters"]["query"]>;
export const listDecisionEvents = (params: DecisionEventQuery) =>
  request<DecisionEventPage>(`/decision-events${query(params)}`);
// The payment run: the bills to pay on a day, weighed against what the reserve leaves.
export type PaymentRun = Schemas["PaymentRunOut"];
export type RunItem = Schemas["RunItem"];
export const getPaymentRun = (on?: string) => request<PaymentRun>(`/payment-runs/current${query({ on })}`);
export type RunApproved = Schemas["RunApproved"];
/** Approves each bill, the evaluation it names; one evaluated again since is refused with why. */
export const approveRun = (items: Schemas["RunApproval"][]) =>
  request<RunApproved>("/payment-runs/approvals", json("POST", { items }));
export type DecisionChain = Schemas["DecisionChainOut"];
export const getDecisionChain = () => request<DecisionChain>("/decision-chain");

// Money (ADR-0026): accounts, their movements, and what the company holds
export type MoneyAccount = Schemas["AccountOut"];
export type Movement = Schemas["MovementOut"];
export type MovementPage = Schemas["MovementPage"];
export type MovementCategory = NonNullable<Movement["category"]>;
export type Holdings = Schemas["Holdings"];
export const listMoneyAccounts = () => request<Schemas["AccountPage"]>("/money/accounts");
type MovementQuery = NonNullable<operations["money.list_movements"]["parameters"]["query"]>;
export const listMovements = (params: MovementQuery) => request<MovementPage>(`/money/movements${query(params)}`);
/** Each bank and cash account's balance, and their total in one currency at the reference rate. */
export const getHoldings = (currency: string) => request<Holdings>(`/money/holdings${query({ currency })}`);
/** The owner's category for a movement, which no sync overwrites. */
export const changeMovement = (id: string, body: Schemas["MovementChange"]) =>
  request<Movement>(`/money/movements/${id}`, json("PATCH", body));

// Messages
export type MessageSummary = Schemas["MessageSummary"];
export type MessagePage = Schemas["MessagePage"];
export type Message = Schemas["MessageOut"];
export type Attachment = Schemas["AttachmentOut"];
export const listMessages = (params: {
  q?: string;
  thread_key?: string;
  channel?: Message["channel"];
  limit?: number;
  offset?: number;
}) =>
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
export const listDocuments = (params: { q?: string; members?: boolean; limit?: number }) =>
  request<DocumentPage>(`/documents${query({ ...params, limit: params.limit ?? 50 })}`);
export type Preview = NonNullable<Document["preview"]>;
export type Workbook = Schemas["WorkbookOut"];
export type Sheet = Schemas["SheetOut"];
export type SheetStyle = Schemas["CellStyleOut"];
export const getDocument = (id: string) => request<DocumentDetail>(`/documents/${id}`);
/** Drawable only as a verified image or PDF; anything else arrives as a download. */
export const getDocumentFile = (id: string) => requestBlob(`/documents/${id}/content`);
export const getDocumentSheets = (id: string) => request<Workbook>(`/documents/${id}/sheets`);
/** The PDF made of a Word, RTF or OpenDocument file, once `converted`. */
export const getDocumentRendition = (id: string) => requestBlob(`/documents/${id}/rendition`);
export type PageHtml = Schemas["HtmlOut"];
/** A web page made safe to show, for `HtmlFrame`. */
export const getDocumentHtml = (id: string) => request<PageHtml>(`/documents/${id}/html`);
export type FileText = Schemas["TextOut"];
/** An XML or text file as text. */
export const getDocumentText = (id: string) => request<FileText>(`/documents/${id}/text`);
export type DocumentPart = Schemas["DocumentPartOut"];
export type UnreadableReason = NonNullable<Document["unreadable_reason"]>;
/** The files taken out of an archive or a winmail.dat, in their order there; none until it is read. */
export const listDocumentParts = (id: string) => request<DocumentPart[]>(`/documents/${id}/parts`);
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

// Chats (ADR-0018): kept on the server, which also holds what was said in each.
export type Chat = Schemas["ChatOut"];
export type ChatPage = Schemas["ChatPage"];
export type ChatMessages = Schemas["ChatMessages"];
export const listChats = (params: { archived?: boolean }) =>
  request<ChatPage>(`/chats${query({ ...params, limit: PAGE })}`);
/** Opens a chat under the id the app chose; sent again, it gives the chat that id already opened. */
export const createChat = (body: Schemas["ChatCreate"]) => request<Chat>("/chats", json("POST", body));
export const updateChat = (id: string, body: Schemas["ChatUpdate"]) =>
  request<Chat>(`/chats/${id}`, json("PATCH", body));
export const deleteChat = (id: string) => request<void>(`/chats/${id}`, { method: "DELETE" });
/** This user has read the chat up to now. */
export const markChatRead = (id: string) => request<void>(`/chats/${id}/read`, { method: "PUT" });
/** Has the chat's earlier part written into notes, which its runs are then sent in its place. */
export const compactChat = (id: string) => request<Chat>(`/chats/${id}/compact`, { method: "POST" });
/** The model and reasoning effort a chat's answers are given, and the others a question may be sent with. */
export type Assistant = Schemas["AssistantOut"];
export const getAssistant = () => request<Assistant>("/assistant");
/** The cards Tamga leaves under what it writes, and the record each shows. */
export type CardKind = Schemas["CardKind"];
export type CardContent = Schemas["CardContent"];
export type NewsItem = Schemas["NewsItem"];
export type TurnContent = Schemas["TurnContent"];
/** A chat's messages as the chat protocol (AG-UI) carries them, oldest first. */
export const getChatMessages = (id: string) => request<ChatMessages>(`/chats/${id}/messages`);
/**
 * Asks in a chat. `init` is the chat protocol's own request (its body, headers and abort signal);
 * the answer is the stream of the protocol's events, for its client to read.
 */
export const runChat = (id: string, init: RequestInit) => send(`/chats/${id}/runs`, init);
/** Stops the answer being written in a chat, when one is. */
export const stopChatRun = (id: string) => request<void>(`/chats/${id}/runs/current`, { method: "DELETE" });

// The playground: a sandbox's scenario, its clock and the world around it.
export type Scenario = Schemas["ScenarioOut"];
export const listScenarios = () => request<Scenario[]>("/playground/scenarios");
export type World = Schemas["WorldOut"];
export type Persona = Schemas["PersonaOut"];
export const getWorld = () => request<World>("/playground/world");
export const skipDays = (days: number) => request<World>("/playground/world/skip", json("POST", { days }));
export type Said = Schemas["Said"];
export const sayAs = (body: Said) => request<void>("/playground/world/messages", json("POST", body));
