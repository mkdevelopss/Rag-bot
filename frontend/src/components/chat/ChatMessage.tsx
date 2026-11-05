import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Bot, Check, Copy, FileText, RotateCw, User } from "lucide-react";
import type { Message, Source } from "../../types";

type Props = {
  message: Message;
  onRegenerate?: () => void;
  isLatestAssistant?: boolean;
  onNotice: (text: string) => void;
};

function SourceCard({ source }: { source: Source }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <button
      className={`source-card ${expanded ? "source-card-open" : ""}`}
      onClick={() => setExpanded((value) => !value)}
      aria-expanded={expanded}
    >
      <span className="source-card-top">
        <FileText size={14} />
        <span className="source-name">{source.filename}</span>
        {source.page_number && <span className="source-page">p. {source.page_number}</span>}
        <span className="source-score">{Math.round(source.similarity * 100)}%</span>
      </span>
      {expanded && <span className="source-snippet">{source.snippet}</span>}
    </button>
  );
}

export default function ChatMessage({ message, onRegenerate, isLatestAssistant, onNotice }: Props) {
  const [copied, setCopied] = useState(false);
  const assistant = message.role === "assistant";

  async function copyAnswer() {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      onNotice("Clipboard access is unavailable in this browser.");
    }
  }

  return (
    <article className={`message-row ${assistant ? "assistant-row" : "user-row"}`}>
      <div className={`avatar ${assistant ? "assistant-avatar" : "user-avatar"}`}>
        {assistant ? <Bot size={17} /> : <User size={16} />}
      </div>
      <div className="message-content">
        <div className="message-author">{assistant ? "Inkwell" : "You"}</div>
        {message.streaming && !message.content ? (
          <div className="typing-indicator"><i /><i /><i /><span>{message.status ?? "Thinking through your documents"}</span></div>
        ) : assistant ? (
          <div className="markdown-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
            {message.streaming && <span className="stream-caret" />}
          </div>
        ) : (
          <div className="user-bubble">{message.content}</div>
        )}
        {assistant && message.sources.length > 0 && (
          <div className="sources-section">
            <div className="sources-label">Sources <span>{message.sources.length}</span></div>
            <div className="source-list">
              {message.sources.map((source) => <SourceCard key={source.chunk_id} source={source} />)}
            </div>
          </div>
        )}
        {assistant && !message.streaming && message.content && (
          <div className="message-actions">
            <button onClick={copyAnswer} title="Copy answer" aria-label="Copy answer">
              {copied ? <Check size={14} /> : <Copy size={14} />}
              <span>{copied ? "Copied" : "Copy"}</span>
            </button>
            {isLatestAssistant && onRegenerate && (
              <button onClick={onRegenerate} title="Regenerate answer">
                <RotateCw size={14} /><span>Regenerate</span>
              </button>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
