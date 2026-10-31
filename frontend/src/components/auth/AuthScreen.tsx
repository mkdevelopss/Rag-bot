import { useState, type FormEvent } from "react";
import { Building2, KeyRound, LockKeyhole, ShieldCheck } from "lucide-react";
import type { UserRecord } from "../../types";

type Props = {
  setupRequired: boolean;
  user: UserRecord | null;
  onLogin: (email: string, password: string) => Promise<void>;
  onChangePassword: (password: string) => Promise<void>;
};

export default function AuthScreen({ setupRequired, user, onLogin, onChangePassword }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (user?.must_change_password) {
        if (newPassword !== confirmation) throw new Error("The new passwords do not match.");
        await onChangePassword(newPassword);
      } else {
        await onLogin(email, password);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="brand-mark"><Building2 size={19} /></div>
        <div className="eyebrow"><span className="eyebrow-line" /> COMPANY KNOWLEDGE</div>
        {setupRequired && !user ? (
          <>
            <h1>Set up your company workspace</h1>
            <p className="page-subtitle">Create the first administrator from the server console. This prevents someone else on the network from claiming admin access.</p>
            <div className="setup-instructions">
              <strong>On the server, open PowerShell in the backend folder:</strong>
              <code>.\.venv\Scripts\python.exe -m app.cli create-admin</code>
              <span>Choose an admin email and a strong password of at least 12 characters. Then return here to sign in.</span>
            </div>
          </>
        ) : user?.must_change_password ? (
          <>
            <h1>Set a new password</h1>
            <p className="page-subtitle">Welcome, {user.display_name}. Replace your temporary password before opening company documents.</p>
            <form className="auth-form" onSubmit={(event) => void submit(event)}>
              <label className="provider-input">
                <span>New password</span>
                <input type="password" autoComplete="new-password" minLength={12} maxLength={256} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required />
              </label>
              <label className="provider-input">
                <span>Confirm new password</span>
                <input type="password" autoComplete="new-password" minLength={12} maxLength={256} value={confirmation} onChange={(event) => setConfirmation(event.target.value)} required />
              </label>
              {error && <p className="auth-error" role="alert">{error}</p>}
              <button className="button-primary auth-submit" disabled={busy || newPassword.length < 12 || !confirmation}>
                <KeyRound size={15} /> {busy ? "Updating…" : "Change password"}
              </button>
            </form>
          </>
        ) : (
          <>
            <h1>Sign in to your company</h1>
            <p className="page-subtitle">Ask questions about documents your administrator has added.</p>
            <form className="auth-form" onSubmit={(event) => void submit(event)}>
              <label className="provider-input">
                <span>Company email</span>
                <input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} required />
              </label>
              <label className="provider-input">
                <span>Password</span>
                <input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required />
              </label>
              {error && <p className="auth-error" role="alert">{error}</p>}
              <button className="button-primary auth-submit" disabled={busy || !email || !password}>
                <LockKeyhole size={15} /> {busy ? "Signing in…" : "Sign in"}
              </button>
            </form>
            <div className="auth-privacy"><ShieldCheck size={14} /> Each employee's chat history is private.</div>
          </>
        )}
      </section>
    </main>
  );
}
