import { ReactNode } from "react";

interface SectionCardProps {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  noPad?: boolean;
}

export function SectionCard({ title, subtitle, actions, children, noPad }: SectionCardProps) {
  return (
    <section className="section-card fade-in">
      <div className="section-head">
        <div className="section-head-left">
          <h2>{title}</h2>
          {subtitle ? <p className="section-subtitle">{subtitle}</p> : null}
        </div>
        {actions ? <div>{actions}</div> : null}
      </div>
      <div className={noPad ? "section-body no-pad" : "section-body"}>
        {children}
      </div>
    </section>
  );
}
