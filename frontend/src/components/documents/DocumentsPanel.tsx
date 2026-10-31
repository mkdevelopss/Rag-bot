import { useRef, useState } from "react";
import { AlertCircle, Check, FileText, FileUp, LoaderCircle, RefreshCw, Trash2, UploadCloud } from "lucide-react";
import type { DocumentRecord } from "../../types";

type Props = {
  documents: DocumentRecord[];
  busy: boolean;
  maxFileSizeMb: number;
  canManage: boolean;
  onUpload: (files: FileList | File[]) => Promise<void>;
  onDelete: (id: string) => void;
  onReprocess: (id: string) => void;
};

function fileSize(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DocumentsPanel({ documents, busy, maxFileSizeMb, canManage, onUpload, onDelete, onReprocess }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  return (
    <section className="page-panel">
      <div className="page-title-row">
        <div>
          <div className="eyebrow"><span className="eyebrow-line" /> COMPANY KNOWLEDGE BASE</div>
          <h1>Company documents</h1>
          <p className="page-subtitle">{canManage ? "Manage the shared files employees use to answer questions." : "Browse the shared files available to answer your questions."}</p>
        </div>
        <div className="doc-count"><FileText size={16} /><strong>{documents.length}</strong><span>{documents.length === 1 ? "file" : "files"}</span></div>
      </div>

      {canManage && <div
        className={`upload-zone ${dragging ? "upload-zone-dragging" : ""} ${busy ? "upload-zone-busy" : ""}`}
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (event.dataTransfer.files.length) void onUpload(event.dataTransfer.files);
        }}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.txt,.md,.csv"
          hidden
          onChange={(event) => {
            if (event.target.files?.length) void onUpload(event.target.files);
            event.target.value = "";
          }}
        />
        <div className="upload-icon">{busy ? <LoaderCircle className="spin" size={22} /> : <UploadCloud size={22} />}</div>
        <strong>{busy ? "Processing your files…" : "Drop your documents here"}</strong>
        <span>PDF, DOCX, TXT, Markdown, or CSV · up to {maxFileSizeMb} MB each</span>
        <button className="button-secondary" disabled={busy} onClick={() => inputRef.current?.click()}>
          <FileUp size={15} /> Browse files
        </button>
      </div>}

      <div className="document-list-heading"><div><h2>Shared library</h2><span>These documents are available to all company employees.</span></div></div>
      {documents.length === 0 ? (
        <div className="documents-empty"><div className="empty-art"><BookOpenIcon /></div><strong>Your library is waiting</strong><span>{canManage ? "Upload a company document to start asking questions." : "Your administrator has not uploaded any company documents yet."}</span></div>
      ) : (
        <div className="document-list">
          {documents.map((document) => (
            <article className="document-row" key={document.document_id}>
              <div className={`document-file-icon file-${document.file_type.toLowerCase()}`}><FileText size={18} /></div>
              <div className="document-info">
                <strong title={document.filename}>{document.filename}</strong>
                <span>{document.file_type} <i /> {fileSize(document.size_bytes)} <i /> {document.chunk_count} chunks</span>
                {document.error && <small className="document-error"><AlertCircle size={12} />{document.error}</small>}
              </div>
              <div className={`document-status status-${document.status}`}>
                {document.status === "ready" ? <Check size={13} /> : document.status === "processing" ? <LoaderCircle className="spin" size={13} /> : <AlertCircle size={13} />}
                {document.status}
              </div>
              {canManage && document.status !== "processing" && (
                <div className="document-actions">
                  <button className="icon-button" title="Reprocess document" onClick={() => onReprocess(document.document_id)}><RefreshCw size={15} /></button>
                  <button className="icon-button danger-hover" title="Delete document" onClick={() => onDelete(document.document_id)}><Trash2 size={15} /></button>
                </div>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function BookOpenIcon() {
  return <FileText size={23} />;
}
