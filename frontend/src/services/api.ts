import type {
  AppSettings, AuthStatus, Conversation, DocumentRecord, EmployeeCreated, Message, UserRecord,
} from "../types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "/api").replace(/\/+$/, "");
let authStatusRequest: Promise<AuthStatus> | null = null;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: init?.credentials ?? "same-origin",
    headers: {
      ...init?.headers,
      ...(init?.method && init.method !== "GET" ? { "X-Inkwell-Request": "1" } : {}),
    },
  });
  if (!response.ok) {
    if (response.status === 401 && !path.endsWith("/auth/login")) {
      window.dispatchEvent(new Event("inkwell:unauthorized"));
    }
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // Keep the HTTP status message when the server does not return JSON.
    }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  authStatus: () => {
    if (!authStatusRequest) {
      authStatusRequest = request<AuthStatus>("/auth/status").finally(() => {
        authStatusRequest = null;
      });
    }
    return authStatusRequest;
  },
  claimAdmin: (adminKey: string) =>
    request<UserRecord>("/auth/claim-admin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ admin_key: adminKey }),
    }),
  login: (email: string, password: string) =>
    request<AuthStatus>("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  changePassword: (newPassword: string) =>
    request<UserRecord>("/auth/change-password", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_password: newPassword }),
    }),
  users: () => request<UserRecord[]>("/auth/users"),
  createEmployee: (email: string, displayName: string) =>
    request<EmployeeCreated>("/auth/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, display_name: displayName }),
    }),
  resetEmployeePassword: (userId: string) =>
    request<EmployeeCreated>(`/auth/users/${userId}/reset-password`, { method: "POST" }),
  setEmployeeActive: (userId: string, isActive: boolean) =>
    request<UserRecord>(`/auth/users/${userId}/active`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: isActive }),
    }),
  health: () =>
    request<{
      status: string;
      llm_provider: "gemini" | "groq";
      llm_model: string;
      llm_configured: boolean;
    }>("/health"),
  conversations: () => request<Conversation[]>("/conversations"),
  conversation: (id: string) => request<Conversation>(`/conversations/${id}`),
  createConversation: () =>
    request<Conversation>("/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    }),
  deleteConversation: (id: string) =>
    request<void>(`/conversations/${id}`, { method: "DELETE" }),
  renameConversation: (id: string, title: string) =>
    request<Conversation>(`/conversations/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }),
  documents: () => request<DocumentRecord[]>("/documents"),
  deleteDocument: (id: string) => request<void>(`/documents/${id}`, { method: "DELETE" }),
  reprocessDocument: (id: string) =>
    request<DocumentRecord>(`/documents/${id}/reprocess`, { method: "POST" }),
  settings: () => request<AppSettings>("/settings"),
  updateSettings: (settings: Partial<AppSettings>) =>
    request<AppSettings>("/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(settings),
    }),
  unlockSettings: (password: string) =>
    request<{ token: string; expires_in: number }>("/settings/unlock", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    }),
  saveProviderSettings: (
    token: string,
    provider: "gemini" | "groq",
    apiKey: string,
    model: string,
  ) =>
    request<AppSettings>("/settings/provider", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ provider, api_key: apiKey || null, model }),
    }),
  lockSettings: (token: string) =>
    request<void>("/settings/lock", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
    }),
};

export async function uploadFiles(files: FileList | File[]) {
  const form = new FormData();
  Array.from(files).forEach((file) => form.append("files", file));
  return request<{ documents: DocumentRecord[]; errors: { filename: string; error: string }[] }>(
    "/documents/upload",
    { method: "POST", body: form },
  );
}

export async function streamChat(
  message: string,
  conversationId: string,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal: AbortSignal,
  regenerate = false,
) {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      "X-Inkwell-Request": "1",
    },
    credentials: "same-origin",
    body: JSON.stringify({ message, conversation_id: conversationId, regenerate }),
    signal,
  });
  if (!response.ok) {
    if (response.status === 401) window.dispatchEvent(new Event("inkwell:unauthorized"));
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // The response may not be JSON on a proxy or connection failure.
    }
    throw new Error(detail);
  }
  if (!response.body) throw new Error("The server did not start a response stream.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentEvent = "message";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const raw of events) {
      let data = "";
      currentEvent = "message";
      for (const line of raw.split("\n")) {
        if (line.startsWith("event:")) currentEvent = line.slice(6).trim();
        if (line.startsWith("data:")) data += line.slice(5).trim();
      }
      if (data) onEvent(currentEvent, JSON.parse(data) as Record<string, unknown>);
    }
    if (done) break;
  }
}

export function messageFromHistory(message: Message): Message {
  return { ...message, sources: message.sources ?? [] };
}
