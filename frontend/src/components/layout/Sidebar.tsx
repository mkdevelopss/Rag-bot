import {
  BookOpen, ChevronRight, Clock3, FileText, MessageSquarePlus,
  Settings2, Sparkles, PanelLeftClose, PanelLeftOpen, Pencil, Trash2,
  LogOut,
} from "lucide-react";
import type { Conversation, UserRecord } from "../../types";

type View = "chat" | "documents" | "settings";

type Props = {
  conversations: Conversation[];
  activeId: string | null;
  collapsed: boolean;
  view: View;
  user: UserRecord;
  onNewChat: () => void;
  onSelectChat: (id: string) => void;
  onView: (view: View) => void;
  onToggle: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string) => void;
  onSignOut: () => void;
};

export default function Sidebar({
  conversations, activeId, collapsed, view, user, onNewChat, onSelectChat, onView, onToggle, onDelete, onRename, onSignOut,
}: Props) {
  return (
    <aside className={`sidebar ${collapsed ? "sidebar-collapsed" : ""}`}>
      <div className="sidebar-brand">
        <div className="brand-mark"><Sparkles size={17} /></div>
        {!collapsed && <div className="brand-name">inkwell<span>.</span><small>DOCUMENT AI</small></div>}
        <button className="icon-button collapse-button" onClick={onToggle} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
          {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
        </button>
      </div>
      <button className="new-chat-button" onClick={onNewChat} title="New chat">
        <MessageSquarePlus size={17} />
        {!collapsed && <><span>New chat</span><kbd>⌘ K</kbd></>}
      </button>
      <nav className="sidebar-nav">
        <button className={`nav-item ${view === "documents" ? "nav-active" : ""}`} onClick={() => onView("documents")} title="Company documents">
          <BookOpen size={17} />{!collapsed && <><span>Company documents</span><span className="nav-chevron"><ChevronRight size={14} /></span></>}
        </button>
        <button className={`nav-item ${view === "settings" ? "nav-active" : ""}`} onClick={() => onView("settings")} title="Settings">
          <Settings2 size={17} />{!collapsed && <><span>Settings</span><span className="nav-chevron"><ChevronRight size={14} /></span></>}
        </button>
      </nav>
      {!collapsed && (
        <div className="history-area">
          <div className="section-heading"><span>RECENTS</span><Clock3 size={13} /></div>
          <div className="history-list">
            {conversations.length === 0 && <div className="history-empty">Your conversations will appear here.</div>}
            {conversations.map((conversation) => (
              <div className="history-entry" key={conversation.conversation_id}>
                <button
                  className={`history-item ${activeId === conversation.conversation_id && view === "chat" ? "history-active" : ""}`}
                  onClick={() => onSelectChat(conversation.conversation_id)}
                  title={conversation.title}
                >
                  <FileText size={15} /><span>{conversation.title}</span>
                </button>
                <span className="history-actions">
                  <button className="history-action" aria-label="Rename conversation" title="Rename" onClick={() => onRename(conversation.conversation_id)}><Pencil size={12} /></button>
                  <button className="history-action" aria-label="Delete conversation" title="Delete" onClick={() => onDelete(conversation.conversation_id)}><Trash2 size={12} /></button>
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="sidebar-bottom">
        <div className="privacy-note">
          <div className="privacy-icon"><BookOpen size={15} /></div>
          {!collapsed && <div><strong>Shared company library</strong><span>Your chat history stays private.</span></div>}
        </div>
        {!collapsed && (
          <div className="sidebar-account">
            <div className="sidebar-footer"><span className="status-dot" /> {user.display_name} · {user.role}</div>
            <button className="sidebar-signout" onClick={onSignOut}><LogOut size={13} /> New private session</button>
          </div>
        )}
      </div>
    </aside>
  );
}
