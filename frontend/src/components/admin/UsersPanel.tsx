import { useState, type FormEvent } from "react";
import { Check, KeyRound, Plus, Shield, UserRound, UserRoundX } from "lucide-react";
import type { EmployeeCreated, UserRecord } from "../../types";

type Props = {
  users: UserRecord[];
  busy: boolean;
  onCreate: (email: string, displayName: string) => Promise<EmployeeCreated>;
  onResetPassword: (userId: string) => Promise<EmployeeCreated>;
  onSetActive: (userId: string, isActive: boolean) => Promise<void>;
};

export default function UsersPanel({ users, busy, onCreate, onResetPassword, onSetActive }: Props) {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [temporaryPassword, setTemporaryPassword] = useState("");
  const [error, setError] = useState("");

  async function createAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      const created = await onCreate(email, displayName);
      setTemporaryPassword(created.temporary_password);
      setEmail("");
      setDisplayName("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function resetPassword(user: UserRecord) {
    if (!window.confirm(`Issue a new temporary password for ${user.email}? Existing sessions will be signed out.`)) return;
    setError("");
    try {
      const reset = await onResetPassword(user.user_id);
      setTemporaryPassword(reset.temporary_password);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function toggleActive(user: UserRecord) {
    const action = user.is_active ? "deactivate" : "reactivate";
    if (!window.confirm(`${action[0].toUpperCase()}${action.slice(1)} ${user.email}?`)) return;
    setError("");
    try {
      await onSetActive(user.user_id, !user.is_active);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <section className="page-panel">
      <div className="page-title-row">
        <div>
          <div className="eyebrow"><span className="eyebrow-line" /> WORKSPACE ADMINISTRATION</div>
          <h1>Employees</h1>
          <p className="page-subtitle">Create employee accounts and control who can sign in to the shared company library.</p>
        </div>
        <div className="settings-title-icon"><Shield size={19} /></div>
      </div>

      <div className="settings-card">
        <div className="settings-card-heading"><div className="settings-card-icon"><Plus size={16} /></div><div><strong>Add an employee</strong><span>A temporary password is created and shown only once.</span></div></div>
        <form className="provider-form" onSubmit={(event) => void createAccount(event)}>
          <label className="provider-input">
            <span>Employee name</span>
            <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} maxLength={120} required />
          </label>
          <label className="provider-input">
            <span>Company email</span>
            <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} maxLength={254} required />
          </label>
          <div className="provider-form-footer">
            <span>Share the temporary password privately. The employee must change it at first sign-in.</span>
            <button className="button-primary" disabled={busy || !email || !displayName.trim()}><Plus size={14} /> {busy ? "Creating…" : "Create account"}</button>
          </div>
        </form>
      </div>

      {temporaryPassword && (
        <div className="temporary-password" role="status">
          <strong>Temporary password — share it now; it will not be shown again.</strong>
          <code>{temporaryPassword}</code>
          <button className="history-action" aria-label="Dismiss temporary password" onClick={() => setTemporaryPassword("")}>×</button>
        </div>
      )}

      <div className="document-list-heading"><div><h2>Company accounts</h2><span>{users.length} accounts</span></div></div>
      <div className="document-list">
        {users.map((user) => (
          <article className="document-row user-row" key={user.user_id}>
            <div className="document-file-icon"><UserRound size={18} /></div>
            <div className="document-info">
              <strong>{user.display_name}</strong>
              <span>{user.email} <i /> {user.role} <i /> {user.is_active ? "active" : "deactivated"}</span>
            </div>
            {user.role === "employee" && (
              <div className="document-actions user-actions">
                <button className="icon-button" title="Issue a new temporary password" onClick={() => void resetPassword(user)}><KeyRound size={15} /></button>
                <button className={`icon-button ${user.is_active ? "danger-hover" : ""}`} title={user.is_active ? "Deactivate employee" : "Reactivate employee"} onClick={() => void toggleActive(user)}>
                  {user.is_active ? <UserRoundX size={15} /> : <Check size={15} />}
                </button>
              </div>
            )}
          </article>
        ))}
      </div>
      {error && <p className="auth-error" role="alert">{error}</p>}
    </section>
  );
}
