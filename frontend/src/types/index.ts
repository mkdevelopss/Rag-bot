export type Source = {
  document_id: string;
  filename: string;
  page_number: number | null;
  snippet: string;
  similarity: number;
  chunk_id: string;
};

export type Message = {
  message_id?: string;
  role: "user" | "assistant";
  content: string;
  sources: Source[];
  created_at?: string;
  streaming?: boolean;
};

export type Conversation = {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages?: Message[];
};

export type DocumentRecord = {
  document_id: string;
  filename: string;
  file_type: string;
  size_bytes: number;
  status: "processing" | "ready" | "failed";
  chunk_count: number;
  created_at: string;
  error?: string | null;
};

export type AppSettings = {
  chunk_size: number;
  chunk_overlap: number;
  top_k: number;
  similarity_threshold: number;
  max_context_chars: number;
  max_file_size_mb: number;
  embedding_model: string;
  llm_provider: "gemini" | "groq";
  llm_model: string;
  provider_configured: boolean;
};

export type UserRecord = {
  user_id: string;
  email: string;
  display_name: string;
  role: "admin" | "employee";
  is_active: boolean;
  must_change_password: boolean;
  created_at: string;
  last_login_at: string | null;
};

export type AuthStatus = {
  authenticated: boolean;
  setup_required: boolean;
  user: UserRecord | null;
};

export type EmployeeCreated = {
  user: UserRecord;
  temporary_password: string;
};
