import { useEffect, useRef, useState } from "react";
import type { CSSProperties, KeyboardEvent, ReactNode } from "react";

// ─── Icon SVG registry ────────────────────────────────────────────────────
function IconSVG({ name, size }: { name: string; size: number }) {
  const icons: Record<string, ReactNode> = {
    dashboard:    <><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></>,
    companies:    <><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></>,
    prospects:    <><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></>,
    leads:        <><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></>,
    settings:     <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></>,
    agents:       <><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></>,
    refresh:      <><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></>,
    close:        <><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></>,
    check:        <><polyline points="20 6 9 17 4 12"/></>,
    eye:          <><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></>,
    edit:         <><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></>,
    trash:        <><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></>,
    plus:         <><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></>,
    chevronDown:  <><polyline points="6 9 12 15 18 9"/></>,
    chevronRight: <><polyline points="9 18 15 12 9 6"/></>,
    sun:          <><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></>,
    moon:         <><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></>,
    pulse:        <><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></>,
    externalLink: <><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></>,
    key:          <><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></>,
    zap:          <><polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/></>,
    user:         <><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></>,
    logOut:       <><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></>,
    globe:        <><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></>,
  };

  if (name === "play") {
    return <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg>;
  }
  if (name === "linkedin") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M16 8a6 6 0 0 1 6 6v7h-4v-7a2 2 0 0 0-2-2 2 2 0 0 0-2 2v7h-4v-7a6 6 0 0 1 6-6z"/>
        <rect x="2" y="9" width="4" height="12"/><circle cx="4" cy="4" r="2"/>
      </svg>
    );
  }

  return (
    <svg width={size} height={size} viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth={2}
      strokeLinecap="round" strokeLinejoin="round">
      {icons[name] ?? icons.pulse}
    </svg>
  );
}

// ─── AppIcon ──────────────────────────────────────────────────────────────
interface AppIconProps {
  name: string;
  size?: number;
  className?: string;
  style?: CSSProperties;
}
export function AppIcon({ name, size = 16, className, style }: AppIconProps) {
  return (
    <span
      className={className}
      style={{ display:"inline-flex", alignItems:"center", justifyContent:"center", width:size, height:size, flexShrink:0, ...style }}
    >
      <IconSVG name={name} size={size} />
    </span>
  );
}

// ─── Spinner ──────────────────────────────────────────────────────────────
export function Spinner({ size = 14 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" className="spin">
      <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
    </svg>
  );
}

// ─── Modal ────────────────────────────────────────────────────────────────
interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  size?: "sm" | "md" | "lg" | "xl";
  footer?: ReactNode;
}
export function Modal({ open, onClose, title, children, size = "md", footer }: ModalProps) {
  const backdropRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const h = (e: globalThis.KeyboardEvent) => { if (e.key === "Escape" && open) onClose(); };
    document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [open, onClose]);

  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => { document.body.style.overflow = ""; };
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="modal-backdrop"
      ref={backdropRef}
      onClick={(e) => { if (e.target === backdropRef.current) onClose(); }}
    >
      <div className={`modal-panel ${size}`} role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <div className="modal-header">
          <span id="modal-title" className="modal-title">{title}</span>
          <button className="modal-close" onClick={onClose} aria-label="Close dialog">
            <AppIcon name="close" size={16} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}

// ─── SectionCard ──────────────────────────────────────────────────────────
interface SectionCardProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
  noPad?: boolean;
  headerAction?: ReactNode;
}
export function SectionCard({ title, subtitle, children, noPad, headerAction }: SectionCardProps) {
  return (
    <div className="section-card">
      <div className="section-card-header">
        <div>
          <div className="section-card-title">{title}</div>
          {subtitle && <div className="section-card-sub">{subtitle}</div>}
        </div>
        {headerAction}
      </div>
      <div className={`section-card-body${noPad ? " no-pad" : ""}`}>{children}</div>
    </div>
  );
}

// ─── Pagination ───────────────────────────────────────────────────────────
interface PaginationProps {
  page: number;
  pageSize: number;
  totalItems: number;
  onPageChange: (p: number) => void;
  onPageSizeChange?: (size: number) => void;
  pageSizeOptions?: number[];
  showPageSize?: boolean;
}
export function Pagination({
  page,
  pageSize,
  totalItems,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 15, 25, 50],
  showPageSize = true,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const start      = totalItems === 0 ? 0 : (page - 1) * pageSize + 1;
  const end        = Math.min(page * pageSize, totalItems);

  const pages: (number | "...")[] = [];
  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) pages.push(i);
  } else {
    pages.push(1);
    if (page > 3) pages.push("...");
    for (let i = Math.max(2, page - 1); i <= Math.min(totalPages - 1, page + 1); i++) pages.push(i);
    if (page < totalPages - 2) pages.push("...");
    pages.push(totalPages);
  }

  return (
    <div style={{ display:"flex", alignItems:"center", gap:12, flexWrap:"wrap" }}>
      {showPageSize && onPageSizeChange && (
        <div className="grid-page-size">
          <span>Rows</span>
          <select
            value={pageSize}
            onChange={(e) => { onPageSizeChange(Number(e.target.value)); onPageChange(1); }}
            aria-label="Rows per page"
          >
            {pageSizeOptions.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </div>
      )}
      {totalItems > 0 && (
        <span className="grid-footer-info">
          {totalItems === 0 ? "No results" : `${start}–${end} of ${totalItems}`}
        </span>
      )}
      {totalPages > 1 && (
        <nav className="pagination" aria-label="Pagination">
          <button className="page-btn" disabled={page <= 1} onClick={() => onPageChange(1)} aria-label="First page">«</button>
          <button className="page-btn" disabled={page <= 1} onClick={() => onPageChange(page - 1)} aria-label="Previous page">‹</button>
          {pages.map((p, i) =>
            p === "..." ? (
              <span key={`e${i}`} style={{ padding:"0 4px", color:"var(--text-disabled)", fontSize:".78rem" }}>…</span>
            ) : (
              <button
                key={p}
                className={`page-btn${page === p ? " active" : ""}`}
                onClick={() => onPageChange(p as number)}
                aria-label={`Page ${p}`}
                aria-current={page === p ? "page" : undefined}
              >
                {p}
              </button>
            )
          )}
          <button className="page-btn" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)} aria-label="Next page">›</button>
          <button className="page-btn" disabled={page >= totalPages} onClick={() => onPageChange(totalPages)} aria-label="Last page">»</button>
        </nav>
      )}
    </div>
  );
}

// ─── UserDropdown ─────────────────────────────────────────────────────────
// Proper popover: click-outside, ESC, keyboard navigation, focus management
interface UserDropdownProps {
  username: string;
  onLogout: () => void;
}
export function UserDropdown({ username, onLogout }: UserDropdownProps) {
  const [open, setOpen]         = useState(false);
  const containerRef            = useRef<HTMLDivElement>(null);
  const triggerRef              = useRef<HTMLButtonElement>(null);
  const firstMenuItemRef        = useRef<HTMLButtonElement>(null);

  const initials = username
    .split(/[\s_-]/)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .slice(0, 2)
    .join("") || "A";

  const close = () => {
    setOpen(false);
    // Return focus to trigger on close
    triggerRef.current?.focus();
  };

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  // Close on ESC; trap Tab inside dropdown
  useEffect(() => {
    if (!open) return;
    const handler = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") { close(); }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open]);

  // Move focus into menu when it opens
  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => { firstMenuItemRef.current?.focus(); });
    }
  }, [open]);

  const handleTriggerKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "Enter" || e.key === " " || e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      setOpen(true);
    }
  };

  return (
    <div ref={containerRef} className="sidebar-footer">
      <button
        ref={triggerRef}
        className="user-menu-trigger"
        onClick={() => setOpen((v) => !v)}
        onKeyDown={handleTriggerKeyDown}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`User menu for ${username}`}
      >
        <div className="user-avatar" aria-hidden="true">{initials}</div>
        <div className="user-info">
          <span className="user-name">{username}</span>
          <span className="user-role">Administrator</span>
        </div>
        <AppIcon
          name="chevronDown"
          size={12}
          className={`user-chevron${open ? " open" : ""}`}
          style={{ color: "var(--text-disabled)", flexShrink: 0 }}
        />
      </button>

      {open && (
        <div
          className="user-dropdown"
          role="menu"
          aria-label="User menu"
          onKeyDown={(e) => {
            // Close on Tab (let focus leave naturally) or Escape
            if (e.key === "Escape") { close(); }
          }}
        >
          <div className="user-dropdown-header" aria-hidden="true">
            <span className="user-dropdown-name">{username}</span>
            <span className="user-dropdown-role">Administrator</span>
          </div>

          <button
            ref={firstMenuItemRef}
            className="user-dropdown-item"
            role="menuitem"
            onClick={() => close()}
          >
            <AppIcon name="user" size={14} aria-hidden="true" />
            Profile
          </button>

          <div className="user-dropdown-divider" role="separator" />

          <button
            className="user-dropdown-item danger"
            role="menuitem"
            onClick={() => { close(); onLogout(); }}
          >
            <AppIcon name="logOut" size={14} aria-hidden="true" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
