import { ReactNode, useEffect } from "react";

interface ModalProps {
  open: boolean;
  title: string;
  subtitle?: string;
  size?: "sm" | "md" | "lg" | "xl";
  onClose: () => void;
  children: ReactNode;
}

export function Modal({ open, title, subtitle, size = "lg", onClose, children }: ModalProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.removeEventListener("keydown", onKey); document.body.style.overflow = prev; };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <section
        className={`modal-panel ${size}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <div>
            <div className="modal-title">{title}</div>
            {subtitle && (
              <div style={{ fontSize: "0.77rem", color: "var(--text-disabled)", marginTop: 3 }}>
                {subtitle}
              </div>
            )}
          </div>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </header>
        <div className="modal-body">{children}</div>
      </section>
    </div>
  );
}