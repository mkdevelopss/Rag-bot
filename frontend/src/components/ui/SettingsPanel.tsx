import { useEffect, useState, type FormEvent } from "react";
import { Check, KeyRound, LockKeyhole, LockOpen, ShieldCheck, SlidersHorizontal } from "lucide-react";
import type { AppSettings, Provider } from "../../types";

const DEFAULT_MODELS: Record<Provider, string> = {
  groq: "openai/gpt-oss-120b",
  gemini: "gemini-3.8-flash",
  openai: "gpt-5-mini",
};

const KEY_PAGES: Record<Provider, { url: string; label: string }> = {
  groq: { url: "https://console.groq.com/keys", label: "Groq Console" },
  gemini: { url: "https://aistudio.google.com/apikey", label: "Google AI Studio" },
  openai: { url: "https://platform.openai.com/api-keys", label: "OpenAI Platform" },
};

type Props = {
  settings: AppSettings | null;
  isAdmin: boolean;
  busy: boolean;
  onClaimAdmin: (adminKey: string) => Promise<boolean>;
  onSave: (settings: Partial<AppSettings>) => Promise<void>;
  onUnlock: (password: string) => Promise<string | null>;
  onSaveProvider: (
    token: string,
    provider: Provider,
    apiKey: string,
    model: string,
  ) => Promise<void>;
  onLock: (token: string) => Promise<void>;
};

export default function SettingsPanel({
  settings, isAdmin, busy, onSave, onUnlock, onSaveProvider, onLock, onClaimAdmin,
}: Props) {
  const [draft, setDraft] = useState<AppSettings | null>(settings);
  const [provider, setProvider] = useState<Provider>(settings?.llm_provider ?? "groq");
  const [model, setModel] = useState(settings?.llm_model ?? "openai/gpt-oss-120b");
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [unlockToken, setUnlockToken] = useState("");
  const [providerBusy, setProviderBusy] = useState(false);
  const [adminKey, setAdminKey] = useState("");
  useEffect(() => setDraft(settings), [settings]);
  useEffect(() => {
    if (!settings) return;
    setProvider(settings.llm_provider);
    setModel(settings.llm_model);
  }, [settings]);
  if (!isAdmin) {
    async function handleAdminClaim(event: FormEvent<HTMLFormElement>) {
      event.preventDefault();
      setProviderBusy(true);
      try {
        if (await onClaimAdmin(adminKey)) setAdminKey("");
      } finally {
        setProviderBusy(false);
      }
    }
    return (
      <section className="page-panel settings-panel">
        <div className="page-title-row">
          <div>
            <div className="eyebrow"><span className="eyebrow-line" /> COMPANY WORKSPACE</div>
            <h1>Settings</h1>
            <p className="page-subtitle">This browser has employee access to the shared company library.</p>
          </div>
          <div className="settings-title-icon"><SlidersHorizontal size={19} /></div>
        </div>
        <details className="settings-card">
          <summary className="settings-card-heading">
            <div className="settings-card-icon"><ShieldCheck size={16} /></div>
            <div><strong>Administrator access</strong><span>For the person who manages company documents and settings.</span></div>
          </summary>
          <form className="provider-form" onSubmit={(event) => void handleAdminClaim(event)}>
            <label className="provider-input">
              <span>One-time administrator key</span>
              <input
                type="password"
                autoComplete="off"
                value={adminKey}
                onChange={(event) => setAdminKey(event.target.value)}
                placeholder="Paste the key generated on the backend"
                required
              />
            </label>
            <div className="provider-form-footer">
              <span>Generate it in the backend folder with <code>python -m app.cli generate-admin-key</code>, then restart the backend. If needed, the command also asks you to choose a separate provider-settings password. The first successful claim disables the key.</span>
              <button className="button-primary" type="submit" disabled={providerBusy || !adminKey}>
                <ShieldCheck size={14} /> {providerBusy ? "Checking…" : "Claim admin"}
              </button>
            </div>
          </form>
        </details>
      </section>
    );
  }
  if (!draft) {
    return (
      <div className="page-panel">
        <p>Settings could not load. Check that the backend is running, then try again.</p>
        <button className="button-primary" onClick={() => window.location.reload()}>Reload app</button>
      </div>
    );
  }

  async function handleUnlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setProviderBusy(true);
    try {
      const token = await onUnlock(password);
      if (token) {
        setUnlockToken(token);
        setPassword("");
      }
    } finally {
      setProviderBusy(false);
    }
  }

  async function handleProviderSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!unlockToken) return;
    setProviderBusy(true);
    try {
      await onSaveProvider(unlockToken, provider, apiKey, model);
      setApiKey("");
    } finally {
      setProviderBusy(false);
    }
  }

  async function handleLock() {
    const token = unlockToken;
    setUnlockToken("");
    setPassword("");
    setApiKey("");
    await onLock(token);
  }

  function chooseProvider(nextProvider: Provider) {
    setProvider(nextProvider);
    setModel(DEFAULT_MODELS[nextProvider]);
    setApiKey("");
  }

  return (
    <section className="page-panel settings-panel">
      <div className="page-title-row">
        <div>
          <div className="eyebrow"><span className="eyebrow-line" /> TUNE YOUR WORKSPACE</div>
          <h1>Settings</h1>
          <p className="page-subtitle">Control how documents are indexed and how answers are retrieved.</p>
        </div>
        <div className="settings-title-icon"><SlidersHorizontal size={19} /></div>
      </div>

      <div className="settings-card">
        <div className="settings-card-heading"><div className="settings-card-icon"><SlidersHorizontal size={16} /></div><div><strong>Document chunking</strong><span>How source text is divided for search.</span></div></div>
        <label className="setting-control">
          <span><strong>Chunk size</strong><small>Characters included in each searchable passage. Larger chunks preserve more context.</small></span>
          <div className="range-control"><input type="range" min="200" max="1600" step="50" value={draft.chunk_size} onChange={(event) => setDraft({ ...draft, chunk_size: Number(event.target.value), chunk_overlap: Math.min(draft.chunk_overlap, Number(event.target.value) - 1) })} /><output>{draft.chunk_size} <small>chars</small></output></div>
        </label>
        <label className="setting-control">
          <span><strong>Chunk overlap</strong><small>Shared text between neighboring passages helps avoid losing context at boundaries.</small></span>
          <div className="range-control"><input type="range" min="0" max={Math.max(50, Math.min(500, draft.chunk_size - 1))} step="10" value={draft.chunk_overlap} onChange={(event) => setDraft({ ...draft, chunk_overlap: Number(event.target.value) })} /><output>{draft.chunk_overlap} <small>chars</small></output></div>
        </label>
        <div className="chunk-explainer"><span className="explainer-dot" /> These settings apply to new uploads. Reprocess existing files from the Documents page to rebuild their chunks.</div>
      </div>

      <div className="settings-card">
        <div className="settings-card-heading"><div className="settings-card-icon"><SlidersHorizontal size={16} /></div><div><strong>Retrieval quality</strong><span>Balance answer relevance and coverage.</span></div></div>
        <label className="setting-control">
          <span><strong>Results to retrieve</strong><small>Maximum number of matching passages to provide to the assistant.</small></span>
          <div className="range-control"><input type="range" min="1" max="12" value={draft.top_k} onChange={(event) => setDraft({ ...draft, top_k: Number(event.target.value) })} /><output>{draft.top_k} <small>passages</small></output></div>
        </label>
        <label className="setting-control">
          <span><strong>Similarity threshold</strong><small>Ignore passages below this semantic match score. Increase to be more selective.</small></span>
          <div className="range-control"><input type="range" min="-0.1" max="0.8" step="0.05" value={draft.similarity_threshold} onChange={(event) => setDraft({ ...draft, similarity_threshold: Number(event.target.value) })} /><output>{draft.similarity_threshold.toFixed(2)}</output></div>
        </label>
      </div>

      <div className="settings-card model-card">
        <div className="settings-card-heading"><div className="settings-card-icon"><span className="model-spark">✦</span></div><div><strong>Models</strong><span>Provider and secret keys are configured on the backend.</span></div></div>
        <div className="model-detail"><span>Embeddings</span><strong>{draft.embedding_model}</strong><small>384-dimensional vectors · processed locally on this machine</small></div>
        <div className="model-detail"><span>Language model · {draft.llm_provider}</span><strong>{draft.llm_model}</strong><small>
          {draft.provider_configured ? "An API key is configured for this provider." : "No API key has been configured yet."}
          {" "}Get a key from{" "}
          <a href={KEY_PAGES[draft.llm_provider].url} target="_blank" rel="noreferrer">
            {KEY_PAGES[draft.llm_provider].label}
          </a>.
        </small></div>
        <div className="model-detail"><span>Choose provider</span><small>
          Credentials are sent to the company backend and saved only in its backend/.env. They are never returned by the API or stored in chat history. Keep the backend private behind HTTPS.
        </small></div>
      </div>

      <div className="settings-card provider-settings-card">
        <div className="settings-card-heading">
          <div className="settings-card-icon"><KeyRound size={16} /></div>
          <div><strong>Provider API key</strong><span>Add or replace the key used to generate answers.</span></div>
          {unlockToken && <span className="unlocked-badge"><ShieldCheck size={13} /> Unlocked</span>}
        </div>
        {!unlockToken ? (
          <form className="provider-form" onSubmit={(event) => void handleUnlock(event)}>
            <label className="provider-input">
              <span>Settings password</span>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="Enter password to unlock"
                required
              />
            </label>
            <div className="provider-form-footer">
              <span>Unlock lasts 15 minutes and is cleared when you close this page.</span>
              <button className="button-primary" type="submit" disabled={providerBusy || !password}>
                <LockOpen size={14} /> {providerBusy ? "Checking…" : "Unlock"}
              </button>
            </div>
          </form>
        ) : (
          <form className="provider-form" onSubmit={(event) => void handleProviderSave(event)}>
            <label className="provider-input">
              <span>Provider</span>
              <select value={provider} onChange={(event) => {
                if (event.target.value in DEFAULT_MODELS) {
                  chooseProvider(event.target.value as Provider);
                }
              }}>
                <option value="groq">Groq</option>
                <option value="gemini">Google Gemini</option>
                <option value="openai">OpenAI</option>
              </select>
            </label>
            <label className="provider-input">
              <span>Model</span>
              <input value={model} onChange={(event) => setModel(event.target.value)} required maxLength={160} />
            </label>
            <label className="provider-input">
              <span>API key</span>
              <input
                type="password"
                autoComplete="new-password"
                value={apiKey}
                onChange={(event) => setApiKey(event.target.value)}
                placeholder={provider === draft.llm_provider && draft.provider_configured ? "Saved key is hidden; leave blank to keep it" : "Paste your provider API key"}
                maxLength={500}
              />
              <small>
                Get a key from{" "}
                <a href={KEY_PAGES[provider].url} target="_blank" rel="noreferrer">
                  {KEY_PAGES[provider].label}
                </a>. Provider free quotas and model access vary by account.
              </small>
            </label>
            <div className="provider-form-footer">
              <span>{draft.provider_configured ? "A key is configured; its value is not displayed." : "No provider key has been saved yet."}</span>
              <div className="provider-form-actions">
                <button className="button-secondary provider-lock-button" type="button" onClick={() => void handleLock()} disabled={providerBusy}>
                  <LockKeyhole size={14} /> Lock
                </button>
                <button
                  className="button-primary"
                  type="submit"
                  disabled={providerBusy || (!apiKey.trim() && (provider !== draft.llm_provider || !draft.provider_configured)) || !model.trim()}
                >
                  <Check size={14} /> {providerBusy ? "Saving…" : "Save provider"}
                </button>
              </div>
            </div>
          </form>
        )}
      </div>

      <div className="settings-save-row">
        <span>Changes are saved to the company workspace.</span>
        <button className="button-primary" disabled={busy} onClick={() => void onSave({
          chunk_size: draft.chunk_size,
          chunk_overlap: draft.chunk_overlap,
          top_k: draft.top_k,
          similarity_threshold: draft.similarity_threshold,
        })}>{busy ? "Saving…" : <><Check size={15} /> Save changes</>}</button>
      </div>
    </section>
  );
}
