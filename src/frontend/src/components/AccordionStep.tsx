import type { ReactNode } from "react";
import type { StepStatus } from "../api/types";

const STATUS_LABEL: Record<StepStatus, string> = {
  pending: "Pending",
  in_progress: "Running",
  done: "Done",
  skipped: "Skipped",
  failed: "Failed",
};

interface Props {
  index: number;
  title: string;
  status: StepStatus;
  current: boolean;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}

export function AccordionStep({ index, title, status, current, open, onToggle, children }: Props) {
  return (
    <section className={`acc${open ? " acc-open" : ""}${current ? " acc-current" : ""}`}>
      <button className="acc-head" onClick={onToggle} aria-expanded={open}>
        {/* The step number doubles as the status light: done ticks, skipped
            dashes, the active step is ringed — so progress reads at a glance
            without scanning the text labels. */}
        <span className={`acc-num acc-num-${status}${current ? " acc-num-current" : ""}`}>
          {status === "done" ? "✓" : status === "skipped" ? "–" : index}
        </span>
        <span className="acc-title">{title}</span>
        <span className={`acc-status acc-status-${status}`}>{STATUS_LABEL[status]}</span>
        <span className="acc-caret">{open ? "▾" : "▸"}</span>
      </button>
      {open && <div className="acc-body">{children}</div>}
    </section>
  );
}
