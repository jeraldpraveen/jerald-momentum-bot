import type { ReactNode } from "react";

export interface TabDef {
  id: string;
  label: string;
  icon?: string;          // single glyph shown in the tile
  hint?: string;          // small line under the label
  badge?: ReactNode;      // e.g. progress "3/8"
}

interface Props {
  tabs: TabDef[];
  active: string;
  onChange: (id: string) => void;
}

// Top-level navigation: Portfolio (persistent state) vs Friday Ritual (the
// weekly cycle). Two different jobs that were previously stacked in one
// accordion, which made the ritual's eight steps hard to scan.
export function Tabs({ tabs, active, onChange }: Props) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button
          key={t.id}
          role="tab"
          aria-selected={active === t.id}
          className={`tab${active === t.id ? " tab-active" : ""}`}
          onClick={() => onChange(t.id)}
        >
          {t.icon && <span className="tab-icon" aria-hidden="true">{t.icon}</span>}
          <span className="tab-text">
            <span className="tab-label">
              {t.label}
              {t.badge != null && <span className="tab-badge">{t.badge}</span>}
            </span>
            {t.hint && <span className="tab-hint">{t.hint}</span>}
          </span>
        </button>
      ))}
    </div>
  );
}
