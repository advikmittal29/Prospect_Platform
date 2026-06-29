import type { ReactNode, CSSProperties } from "react";

export type IconName =
  | "dashboard"
  | "jobs"
  | "companies"
  | "candidates"
  | "prospects"
  | "agents"
  | "controls"
  | "settings"
  | "spark"
  | "pulse"
  | "check"
  | "clock"
  | "warning"
  | "close"
  | "logout";

interface IconProps {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
}

function wrap(path: ReactNode) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {path}
    </svg>
  );
}

export function AppIcon({ name, size = 18, className, style }: IconProps) {
  const icon = (() => {
    switch (name) {
      case "dashboard":
        return wrap(
          <>
            <rect x="3" y="3" width="8" height="8" rx="2" />
            <rect x="13" y="3" width="8" height="5" rx="2" />
            <rect x="13" y="10" width="8" height="11" rx="2" />
            <rect x="3" y="13" width="8" height="8" rx="2" />
          </>
        );
      case "jobs":
        return wrap(
          <>
            <rect x="3" y="7" width="18" height="13" rx="2" />
            <path d="M9 7V5a3 3 0 0 1 6 0v2" />
            <path d="M3 12h18" />
          </>
        );
      case "companies":
        return wrap(
          <>
            <path d="M4 21V6a1 1 0 0 1 1-1h6v16" />
            <path d="M11 21V3a1 1 0 0 1 1-1h7a1 1 0 0 1 1 1v18" />
            <path d="M8 9h0M8 13h0M15 7h0M15 11h0M15 15h0" />
          </>
        );
      case "candidates":
        return wrap(
          <>
            <circle cx="8" cy="8" r="3" />
            <circle cx="16" cy="8" r="3" />
            <path d="M3 19a5 5 0 0 1 10 0" />
            <path d="M11 19a5 5 0 0 1 10 0" />
          </>
        );
      case "prospects":
        return wrap(
          <>
            <circle cx="9" cy="8" r="3" />
            <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
            <circle cx="18" cy="10" r="2.5" />
            <path d="M15.5 19a4.5 4.5 0 0 1 5 0" />
          </>
        );
      case "agents":
        return wrap(
          <>
            <path d="M4 20V9a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v11" />
            <path d="M8 7V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v3" />
            <path d="M12 12h.01M9 16h6" />
          </>
        );
      case "controls":
        return wrap(
          <>
            <circle cx="7" cy="7" r="3" />
            <circle cx="17" cy="17" r="3" />
            <path d="M10 7h11" />
            <path d="M3 17h11" />
          </>
        );
      case "settings":
        return wrap(
          <>
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1 1 0 0 0 .2 1.1l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1 1 0 0 0-1.1-.2 1 1 0 0 0-.6.9V20a2 2 0 1 1-4 0v-.2a1 1 0 0 0-.6-.9 1 1 0 0 0-1.1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1 1 0 0 0 .2-1.1 1 1 0 0 0-.9-.6H4a2 2 0 1 1 0-4h.2a1 1 0 0 0 .9-.6 1 1 0 0 0-.2-1.1l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1 1 0 0 0 1.1.2h0a1 1 0 0 0 .6-.9V4a2 2 0 1 1 4 0v.2a1 1 0 0 0 .6.9h0a1 1 0 0 0 1.1-.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1 1 0 0 0-.2 1.1v0a1 1 0 0 0 .9.6H20a2 2 0 1 1 0 4h-.2a1 1 0 0 0-.9.6Z" />
          </>
        );
      case "spark":
        return wrap(<path d="M4 16 9 11l3 3 6-7" />);
      case "pulse":
        return wrap(<path d="M3 12h4l2.5-5 4 10 2.5-5H21" />);
      case "check":
        return wrap(<path d="m5 13 4 4L19 7" />);
      case "clock":
        return wrap(
          <>
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7v5l3 2" />
          </>
        );
      case "warning":
        return wrap(
          <>
            <path d="M12 3 2.8 19a1 1 0 0 0 .9 1.5h16.6a1 1 0 0 0 .9-1.5L12 3Z" />
            <path d="M12 9v4" />
            <circle cx="12" cy="16" r=".6" fill="currentColor" stroke="none" />
          </>
        );
      case "close":
        return wrap(
          <>
            <path d="M18 6 6 18M6 6l12 12" />
          </>
        );
      case "logout":
        return wrap(
          <>
            <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
            <polyline points="16 17 21 12 16 7" />
            <line x1="21" y1="12" x2="9" y2="12" />
          </>
        );
      default:
        return wrap(<circle cx="12" cy="12" r="9" />);
    }
  })();

  return (
    <span className={className} style={{ width: size, height: size, display: "inline-flex", ...style }} aria-hidden="true">
      {icon}
    </span>
  );
}

