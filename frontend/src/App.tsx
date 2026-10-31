import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import {
  ArrowUp, BookOpen, Check, ChevronDown, Command, FilePlus2,
  FileText, Moon, Plus, ShieldCheck, Sparkles, Sun, Trash2, LogOut,
} from "lucide-react";
import AuthScreen from "./components/auth/AuthScreen";
import ChatMessage from "./components/chat/ChatMessage";
import DocumentsPanel from "./components/documents/DocumentsPanel";
import Sidebar from "./components/layout/Sidebar";
import SettingsPanel from "./components/ui/SettingsPanel";
import { api, messageFromHistory, streamChat, uploadFiles } from "./services/api";
import type {
  AppSettings, AuthStatus, Conversation, DocumentRecord, Message, Source, UserRecord,
} from "./types";

type View = "chat" | "documents" | "settings";

const examples = [
  { icon: "01", title: "Give me the key takeaways", prompt: "What are the key takeaways from my documents?" },
  { icon: "02", title: "Compare two ideas", prompt: "Compare the main ideas covered across my documents." },
  { icon: "03", title: "Find a specific detail", prompt: "What important details should I know from the documents?" },
];

export default function App() {
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [authError, setAuthError] = useState("");

  useEffect(() => {
    let active = true;
    const handleUnauthorized = () => {
      void api.authStatus().then((status) => {
        if (active) setAuthStatus(status);
      }).catch((error) => {
        if (active) setAuthError(String(error));
      });
    };
    window.addEventListener("inkwell:unauthorized", handleUnauthorized);
    void api.authStatus().then((status) => {
      if (active) {
        setAuthStatus(status);
        setAuthError("");
      }
    }).catch((error) => {
      if (active) setAuthError(String(error));
    });
    return () => {
      active = false;
      window.removeEventListener("inkwell:unauthorized", handleUnauthorized);
    };
  }, []);

  async function signIn(email: string, password: string) {
    setAuthStatus(await api.login(email, password));
  }

  async function changePassword(password: string) {
    await api.changePassword(password);
    setAuthStatus(await api.authStatus());
  }

  async function signOut() {
    setAuthStatus(await api.authStatus());
  }

  if (!authStatus) {
    return (
      <main className="auth-page">
        <section className="auth-card">
          <div className="brand-mark"><ShieldCheck size={19} /></div>
          <h1>{authError ? "Company workspace unavailable" : "Opening company workspace…"}</h1>
          {authError && <><p className="auth-error">{authError}</p><button className="button-primary" onClick={() => window.location.reload()}>Try again</button></>}
        </section>
      </main>
    );
  }

  if (!authStatus.authenticated || !authStatus.user || authStatus.user.must_change_password) {
    return (
      <AuthScreen
        setupRequired={authStatus.setup_required}
        user={authStatus.user}
        onLogin={signIn}
        onChangePassword={changePassword}
      />
    );
  }

  return (
    <Workspace
      key={authStatus.user.user_id}
      currentUser={authStatus.user}
      onSignOut={() => void signOut()}
      onAdminClaimed={(user) => setAuthStatus((current) => current
        ? { ...current, authenticated: true, user }
        : current)}
    />
  );
}

function Workspace({
  currentUser,
  onSignOut,
  onAdminClaimed,
}: {
  currentUser: UserRecord;
  onSignOut: () => void;
  onAdminClaimed: (user: UserRecord) => void;
}) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [view, setView] = useState<View>("chat");
  const [collapsed, setCollapsed] = useState(false);
  const [darkMode, setDarkMode] = useState(true);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [llmReady, setLlmReady] = useState<boolean | null>(null);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const streamAbortRef = useRef<AbortController | null>(null);

  const notify = useCallback((text: string) => {
    setNotice(text);
    window.setTimeout(() => setNotice(""), 3800);
  }, []);

  async function handleSignOut() {
    try {
      await api.logout();
      onSignOut();
    } catch (error) {
      notify(`Could not sign out from the server: ${String(error)}`);
    }
  }

  const refreshDocuments = useCallback(async () => {
    setDocuments(await api.documents());
  }, []);

  const refreshConversations = useCallback(async () => {
    setConversations(await api.conversations());
  }, []);

  useEffect(() => {
    let active = true;
    async function initialize() {
      try {
        const [conversationList, documentList, appSettings, health] = await Promise.all([
          api.conversations(),
          api.documents(),
          currentUser.role === "admin" ? api.settings() : Promise.resolve(null),
          api.health(),
        ]);
        if (!active) return;
        setConversations(conversationList);
        setDocuments(documentList);
        setSettings(appSettings);
        setLlmReady(health.llm_configured);
        if (conversationList.length) {
          const first = await api.conversation(conversationList[0].conversation_id);
          if (!active) return;
          setActiveId(first.conversation_id);
          setMessages((first.messages ?? []).map(messageFromHistory));
        } else {
          const created = await api.createConversation();
          if (!active) return;
          setConversations([created]);
          setActiveId(created.conversation_id);
        }
      } catch (error) {
        if (active) notify(`Could not connect to the backend: ${String(error)}`);
      } finally {
        if (active) setLoading(false);
      }
    }
    void initialize();
    return () => {
      active = false;
      streamAbortRef.current?.abort();
    };
  }, [notify, currentUser.role]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    if (!documents.some((document) => document.status === "processing")) return;
    const timer = window.setInterval(() => { void refreshDocuments().catch((error) => notify(String(error))); }, 2500);
    return () => window.clearInterval(timer);
  }, [documents, notify, refreshDocuments]);

  useEffect(() => {
    function handleShortcut(event: globalThis.KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        document.querySelector<HTMLButtonElement>(".new-chat-button")?.click();
      }
    }
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);

  async function selectConversation(id: string) {
    try {
      const conversation = await api.conversation(id);
      setActiveId(id);
      setMessages((conversation.messages ?? []).map(messageFromHistory));
      setView("chat");
    } catch (error) {
      notify(`Could not open conversation: ${String(error)}`);
    }
  }

  async function createConversation() {
    try {
      const conversation = await api.createConversation();
      setConversations((current) => [conversation, ...current]);
      setActiveId(conversation.conversation_id);
      setMessages([]);
      setView("chat");
      window.setTimeout(() => inputRef.current?.focus(), 0);
    } catch (error) {
      notify(`Could not create a conversation: ${String(error)}`);
    }
  }

  async function clearConversation() {
    if (!activeId) return;
    try {
      await api.deleteConversation(activeId);
      const next = await api.createConversation();
      setConversations((current) => [next, ...current.filter((item) => item.conversation_id !== activeId)]);
      setActiveId(next.conversation_id);
      setMessages([]);
      notify("Conversation cleared.");
    } catch (error) {
      notify(`Could not clear the conversation: ${String(error)}`);
    }
  }

  async function deleteConversation(id: string) {
    try {
      await api.deleteConversation(id);
      const remaining = conversations.filter((item) => item.conversation_id !== id);
      setConversations(remaining);
      if (activeId === id) {
        if (remaining.length) await selectConversation(remaining[0].conversation_id);
        else {
          setActiveId(null);
          setMessages([]);
          await createConversation();
        }
      }
      notify("Conversation deleted.");
    } catch (error) {
      notify(`Could not delete the conversation: ${String(error)}`);
    }
  }

  async function renameConversation(id: string) {
    const current = conversations.find((item) => item.conversation_id === id);
    const title = window.prompt("Rename conversation", current?.title ?? "");
    if (!title?.trim()) return;
    try {
      await api.renameConversation(id, title.trim());
      await refreshConversations();
    } catch (error) {
      notify(`Could not rename the conversation: ${String(error)}`);
    }
  }

  async function sendMessage(value = input, regenerate = false) {
    const text = value.trim();
    if (!text || sending) return;
    let conversationId = activeId;
    if (regenerate && !conversationId) return;
    if (!conversationId) {
      try {
        const conversation = await api.createConversation();
        conversationId = conversation.conversation_id;
        setActiveId(conversationId);
        setConversations((current) => [conversation, ...current]);
      } catch (error) {
        notify(`Could not start a conversation: ${String(error)}`);
        return;
      }
    }
    setInput("");
    if (inputRef.current) inputRef.current.style.height = "auto";
    setView("chat");
    setSending(true);
    const assistantIndex = regenerate ? messages.length - 1 : messages.length + 1;
    setMessages((current) => regenerate
      ? current.map((item, index) => index === assistantIndex
        ? { role: "assistant", content: "", sources: [], streaming: true }
        : item)
      : [
        ...current,
        { role: "user", content: text, sources: [] },
        { role: "assistant", content: "", sources: [], streaming: true },
      ]);
    const controller = new AbortController();
    streamAbortRef.current = controller;
    try {
      await streamChat(text, conversationId, (event, data) => {
        if (event === "meta" && typeof data.conversation_id === "string") {
          setActiveId(data.conversation_id);
        } else if (event === "sources" && Array.isArray(data.sources)) {
          const sources = data.sources as Source[];
          setMessages((current) => current.map((item, index) =>
            index === assistantIndex ? { ...item, sources } : item));
        } else if (event === "delta" && typeof data.text === "string") {
          setMessages((current) => current.map((item, index) =>
            index === assistantIndex ? { ...item, content: item.content + data.text } : item));
        } else if (event === "done") {
          setMessages((current) => current.map((item, index) =>
            index === assistantIndex ? { ...item, streaming: false } : item));
        } else if (event === "error") {
          const message = typeof data.message === "string" ? data.message : "The response stream failed.";
          setMessages((current) => current.map((item, index) =>
            index === assistantIndex ? { ...item, content: item.content ? `${item.content}\n\n> ${message}` : `**Unable to answer:** ${message}`, streaming: false } : item));
          notify(message);
        }
      }, controller.signal, regenerate);
      await refreshConversations();
    } catch (error) {
      if (!controller.signal.aborted) {
        const message = String(error);
        setMessages((current) => current.map((item, index) =>
          index === assistantIndex ? { ...item, content: `**Unable to answer:** ${message}`, streaming: false } : item));
        notify(message);
      }
    } finally {
      setSending(false);
      streamAbortRef.current = null;
      void refreshConversations().catch(() => undefined);
      inputRef.current?.focus();
    }
  }

  async function handleUpload(files: FileList | File[]) {
    if (!files.length) return;
    setUploading(true);
    try {
      const result = await uploadFiles(files);
      setDocuments((current) => [
        ...result.documents.filter((uploaded) => !current.some((item) => item.document_id === uploaded.document_id)),
        ...current,
      ]);
      await refreshDocuments();
      const messages = [
        ...result.documents.map((document) =>
          document.status === "ready"
            ? `${document.filename}: ${document.chunk_count} chunks indexed`
            : document.status === "processing"
              ? `${document.filename}: uploaded; indexing in progress`
              : `${document.filename}: ${document.error || "upload failed"}`,
        ),
        ...result.errors.map((item) => `${item.filename}: ${item.error}`),
      ];
      notify(messages.join(" · ") || "Upload complete.");
    } catch (error) {
      notify(`Upload failed: ${String(error)}`);
    } finally {
      setUploading(false);
    }
  }

  async function deleteDocument(id: string) {
    try {
      await api.deleteDocument(id);
      await refreshDocuments();
      notify("Document and its vectors deleted.");
    } catch (error) {
      notify(`Could not delete the document: ${String(error)}`);
    }
  }

  async function reprocessDocument(id: string) {
    try {
      await api.reprocessDocument(id);
      await refreshDocuments();
      notify("Document reprocessed.");
    } catch (error) {
      notify(`Could not reprocess the document: ${String(error)}`);
    }
  }

  async function saveSettings(updates: Partial<AppSettings>) {
    setSavingSettings(true);
    try {
      setSettings(await api.updateSettings(updates));
      notify("Settings saved.");
    } catch (error) {
      notify(`Could not save settings: ${String(error)}`);
    } finally {
      setSavingSettings(false);
    }
  }

  async function unlockProviderSettings(password: string): Promise<string | null> {
    try {
      const session = await api.unlockSettings(password);
      notify("Provider settings unlocked for 15 minutes.");
      return session.token;
    } catch (error) {
      notify(`Could not unlock provider settings: ${String(error)}`);
      return null;
    }
  }

  async function saveProviderSettings(
    token: string,
    provider: "gemini" | "groq",
    apiKey: string,
    model: string,
  ) {
    try {
      const updated = await api.saveProviderSettings(token, provider, apiKey, model);
      setSettings(updated);
      setLlmReady(updated.provider_configured);
      notify(`${provider === "groq" ? "Groq" : "Gemini"} settings saved. The key stays on the backend.`);
    } catch (error) {
      notify(`Could not save provider settings: ${String(error)}`);
    }
  }

  async function lockProviderSettings(token: string) {
    if (!token) return;
    try {
      await api.lockSettings(token);
      notify("Provider settings locked.");
    } catch (error) {
      notify(`Could not lock provider settings: ${String(error)}`);
    }
  }

  async function claimAdmin(adminKey: string): Promise<boolean> {
    try {
      const user = await api.claimAdmin(adminKey);
      onAdminClaimed(user);
      notify("Administrator access claimed for this browser session.");
      return true;
    } catch (error) {
      notify(`Could not claim administrator access: ${String(error)}`);
      return false;
    }
  }

  function regenerateLast() {
    const lastQuestion = [...messages].reverse().find((item) => item.role === "user");
    if (lastQuestion) void sendMessage(lastQuestion.content, true);
  }

  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  const activeConversation = conversations.find((item) => item.conversation_id === activeId);

  return (
    <div className={`app-shell ${darkMode ? "" : "light-mode"}`}>
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        collapsed={collapsed}
        view={view}
        user={currentUser}
        onNewChat={() => void createConversation()}
        onSelectChat={(id) => void selectConversation(id)}
        onView={setView}
        onToggle={() => setCollapsed((value) => !value)}
        onDelete={(id) => void deleteConversation(id)}
        onRename={(id) => void renameConversation(id)}
        onSignOut={() => void handleSignOut()}
      />
      <main className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <span>Workspace</span><span className="breadcrumb-slash">/</span>
            <strong>{view === "chat" ? activeConversation?.title ?? "New conversation" : view === "documents" ? "Company documents" : "Settings"}</strong>
            <ChevronDown size={14} />
          </div>
          <div className="topbar-right">
            {llmReady === false && (
              <button
                className="api-warning"
                onClick={() => setView("settings")}
              >
                <span /> {currentUser.role === "admin" ? "API key needed" : "AI provider needs setup"}
              </button>
            )}
            <button className="icon-button theme-toggle" title={darkMode ? "Switch to light theme" : "Switch to dark theme"} onClick={() => setDarkMode((value) => !value)}>
              {darkMode ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <div className="user-avatar-small" title={`${currentUser.display_name} (${currentUser.role})`}>{currentUser.display_name[0]?.toUpperCase() ?? "U"}</div>
            <button className="icon-button" title="Sign out" onClick={() => void handleSignOut()}><LogOut size={16} /></button>
          </div>
        </header>

        {view === "chat" ? (
          <div className="chat-page">
            <div className="chat-scroll-area" ref={scrollRef}>
              {loading ? (
                <div className="loading-view"><div className="brand-mark"><Sparkles size={18} /></div><span>Opening your workspace…</span></div>
              ) : messages.length === 0 ? (
                <div className="welcome-wrap">
                  <div className="welcome-kicker"><span className="welcome-orbit"><Sparkles size={13} /></span> YOUR KNOWLEDGE, IN CONVERSATION</div>
                  <h1>Good ideas live in<br /><em>your documents.</em></h1>
                  <p className="welcome-intro">Ask questions across your files. Get clear answers, grounded in the words that matter.</p>
                  <div className="welcome-stats">
                    <span><FileText size={14} /> {documents.length} {documents.length === 1 ? "document" : "documents"} in your library</span>
                    <i />
                    <span><ShieldCheck size={14} /> Source-backed answers</span>
                  </div>
                  <div className="prompt-grid">
                    {examples.map((example) => (
                      <button key={example.icon} className="prompt-card" onClick={() => void sendMessage(example.prompt)}>
                        <span className="prompt-number">{example.icon}</span>
                        <strong>{example.title}</strong>
                        <span>{example.prompt}</span>
                        <ArrowUp size={15} className="prompt-arrow" />
                      </button>
                    ))}
                  </div>
                  {documents.length === 0 && (currentUser.role === "admin"
                    ? <button className="empty-upload-cta" onClick={() => setView("documents")}><FilePlus2 size={16} /> Add your first document</button>
                    : <div className="welcome-footnote">Your administrator has not added any company documents yet.</div>)}
                  <div className="welcome-footnote"><span className="status-dot" /> Answers cite your documents. No source, no guess.</div>
                </div>
              ) : (
                <div className="message-list">
                  {messages.map((message, index) => (
                    <ChatMessage
                      key={message.message_id ?? `${index}-${message.role}`}
                      message={message}
                      isLatestAssistant={index === messages.length - 1 && message.role === "assistant"}
                      onRegenerate={regenerateLast}
                      onNotice={notify}
                    />
                  ))}
                </div>
              )}
            </div>
            <div className="composer-wrap">
              <form className="composer" onSubmit={(event) => { event.preventDefault(); void sendMessage(); }}>
                <textarea
                  ref={inputRef}
                  rows={1}
                  value={input}
                  disabled={sending || loading}
                  onChange={(event) => {
                    setInput(event.target.value);
                    event.target.style.height = "auto";
                    event.target.style.height = `${Math.min(event.target.scrollHeight, 180)}px`;
                  }}
                  onKeyDown={onComposerKeyDown}
                  placeholder="Ask anything about your documents…"
                  aria-label="Message"
                />
                <div className="composer-bottom">
                  <div className="composer-tools">
                    {currentUser.role === "admin" && (
                      <>
                        <input ref={fileInputRef} type="file" multiple hidden accept=".pdf,.docx,.txt,.md,.csv" onChange={(event) => { if (event.target.files) void handleUpload(event.target.files); event.target.value = ""; }} />
                        <button type="button" className="composer-tool" title="Upload company documents" onClick={() => fileInputRef.current?.click()}><Plus size={16} /><span>Add files</span></button>
                        <span className="composer-separator" />
                      </>
                    )}
                    <span className="grounded-label"><ShieldCheck size={13} /> Grounded in your library</span>
                  </div>
                  <div className="composer-right">
                    <span className="keyboard-hint"><kbd>↵</kbd> send <kbd>⇧ ↵</kbd> new line</span>
                    <button type="submit" className={`send-button ${input.trim() ? "send-ready" : ""}`} disabled={!input.trim() || sending || loading} aria-label="Send message">
                      {sending ? <div className="small-spinner" /> : <ArrowUp size={17} />}
                    </button>
                  </div>
                </div>
              </form>
              <div className="composer-disclaimer">Inkwell can make mistakes. Check the cited passages for important decisions.</div>
            </div>
          </div>
        ) : view === "documents" ? (
          <DocumentsPanel documents={documents} busy={uploading} maxFileSizeMb={settings?.max_file_size_mb ?? 20} canManage={currentUser.role === "admin"} onUpload={handleUpload} onDelete={(id) => void deleteDocument(id)} onReprocess={(id) => void reprocessDocument(id)} />
        ) : (
          <SettingsPanel
            settings={settings}
            isAdmin={currentUser.role === "admin"}
            busy={savingSettings}
            onSave={saveSettings}
            onUnlock={unlockProviderSettings}
            onSaveProvider={saveProviderSettings}
            onLock={lockProviderSettings}
            onClaimAdmin={claimAdmin}
          />
        )}

        {view === "chat" && (
          <div className="chat-header-actions">
            <button className="icon-button" title="Upload documents" onClick={() => setView("documents")}><BookOpen size={16} /></button>
            <button className="icon-button" title="Clear conversation" onClick={() => void clearConversation()}><Trash2 size={16} /></button>
          </div>
        )}
      </main>
      {notice && <div className="toast"><Check size={16} /><span>{notice}</span><button onClick={() => setNotice("")}>×</button></div>}
      <div className="shortcuts-hint"><Command size={12} /> Inkwell <span>·</span> Company workspace</div>
    </div>
  );
}
