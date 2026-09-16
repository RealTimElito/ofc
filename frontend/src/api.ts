export type LlmProfile = {
  id: number;
  name: string;
  base_url: string;
  model: string;
  temperature: number;
  max_tokens: number;
  system_prompt: string | null;
  is_default: boolean;
  has_api_key: boolean;
  created_at: string;
};

export type UploadedFile = {
  id: number;
  original_name: string;
  mime_type: string | null;
  size_bytes: number;
  role: string;
  created_at: string;
};

export type DbConnection = {
  id: number;
  name: string;
  dialect: string;
  notes: string | null;
  created_at: string;
};

export type SavedQuery = {
  id: number;
  connection_id: number;
  name: string;
  purpose: string;
  sql_text: string;
  example_body_column: string | null;
  created_at: string;
};

export type LibraryDocument = {
  id: number;
  title: string;
  filename: string;
  format: string;
  body_md: string;
  source_report_id: number | null;
  role: string;
  created_at: string;
  updated_at: string;
};

export type ReportProject = {
  id: number;
  title: string;
  brief: string;
  file_ids: number[];
  query_ids: number[];
  document_ids: number[];
  example_file_ids: number[];
  use_all_examples: boolean;
  llm_profile_id: number | null;
  status: string;
  style_notes_md?: string;
  outline_md: string;
  body_md: string;
  critique_md: string;
  created_at: string;
  updated_at: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail ?? JSON.stringify(j);
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return (await res.text()) as T;
}

export const api = {
  health: () => request<{ status: string; llm_configured: boolean }>("/api/health"),
  settings: () =>
    request<{ llm_base_url: string; llm_model: string; data_dir: string; air_gapped: boolean }>(
      "/api/settings",
    ),

  listProfiles: () => request<LlmProfile[]>("/api/llm/profiles"),
  createProfile: (body: Record<string, unknown>) =>
    request<LlmProfile>("/api/llm/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteProfile: (id: number) => request(`/api/llm/profiles/${id}`, { method: "DELETE" }),
  pingProfile: (id: number) =>
    request<{ ok: boolean; status_code: number }>(`/api/llm/profiles/${id}/ping`, {
      method: "POST",
    }),
  pingDefault: () =>
    request<{ ok: boolean; status_code: number }>("/api/llm/ping-default", { method: "POST" }),

  listFiles: () => request<UploadedFile[]>("/api/files"),
  uploadFile: async (file: File, role: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("role", role);
    return request<UploadedFile>("/api/files", { method: "POST", body: fd });
  },
  updateFileRole: (id: number, role: string) =>
    request<UploadedFile>(`/api/files/${id}?role=${encodeURIComponent(role)}`, {
      method: "PATCH",
    }),
  deleteFile: (id: number) => request(`/api/files/${id}`, { method: "DELETE" }),

  listDocuments: () => request<LibraryDocument[]>("/api/documents"),
  createDocument: (body: Record<string, unknown>) =>
    request<LibraryDocument>("/api/documents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteDocument: (id: number) => request(`/api/documents/${id}`, { method: "DELETE" }),

  listConnections: () => request<DbConnection[]>("/api/db/connections"),
  createConnection: (body: Record<string, unknown>) =>
    request<DbConnection>("/api/db/connections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteConnection: (id: number) =>
    request(`/api/db/connections/${id}`, { method: "DELETE" }),

  listQueries: () => request<SavedQuery[]>("/api/db/queries"),
  createQuery: (body: Record<string, unknown>) =>
    request<SavedQuery>("/api/db/queries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteQuery: (id: number) => request(`/api/db/queries/${id}`, { method: "DELETE" }),
  previewQuery: (id: number) =>
    request<{ columns: string[]; rows: Record<string, unknown>[]; row_count: number }>(
      `/api/db/queries/${id}/preview`,
      { method: "POST" },
    ),

  listReports: () => request<ReportProject[]>("/api/reports"),
  getReport: (id: number) => request<ReportProject>(`/api/reports/${id}`),
  createReport: (body: Record<string, unknown>) =>
    request<ReportProject>("/api/reports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  updateReport: (id: number, body: Record<string, unknown>) =>
    request<ReportProject>(`/api/reports/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  deleteReport: (id: number) => request(`/api/reports/${id}`, { method: "DELETE" }),
  generate: (id: number, stage = "full", with_critique = true) =>
    request<ReportProject>(`/api/reports/${id}/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stage, with_critique }),
    }),
  markDone: (id: number, role = "example") =>
    request<{ report: ReportProject; document: LibraryDocument }>(`/api/reports/${id}/done`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    }),
};
