import { AppIcon, type IconName } from "./AppIcon";

interface StatCardProps {
  label: string;
  value: number | string;
  accent?: "sun" | "sea" | "mint" | "slate" | "royal" | "sky" | "violet";
  icon?: IconName;
  trendLabel?: string;
  progress?: number;
}

// Map legacy accent names to new system
const colorMap: Record<string, string> = {
  sun: "amber", sea: "blue", mint: "green", slate: "violet",
  royal: "teal", sky: "blue", violet: "violet",
};

function clamp(v?: number) {
  if (v == null || isNaN(v)) return 0;
  return Math.max(0, Math.min(100, Math.round(v)));
}

export function StatCard({ label, value, accent = "slate", icon = "spark", trendLabel, progress }: StatCardProps) {
  const color = colorMap[accent] ?? "blue";
  const pct = clamp(progress);

  return (
    <article className={`kpi-card ${color}`}>
      <div className="kpi-top">
        <div>
          <p className="kpi-label">{label}</p>
          <p className="kpi-value">{value}</p>
        </div>
        <span className="kpi-icon">
          <AppIcon name={icon} size={14} />
        </span>
      </div>
      <div className="kpi-trend">{trendLabel ?? `${pct}%`}</div>
      <div className="kpi-bar-track">
        <div className="kpi-bar-fill" style={{ width: `${pct}%` }} />
      </div>
    </article>
  );
}
