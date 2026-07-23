import { useEffect, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { Cycle, CycleSteps, CycleSummary, StepEnvelope, StepStatus } from "../../api/types";
import { StepDetail } from "./StepDetail";

const STEP_TITLES: { id: keyof CycleSteps; title: string }[] = [
  { id: "part_a_regime_gate", title: "Regime Gate" },
  { id: "part_b_scanner", title: "Jerald Score" },
  { id: "part_c_exit_flags", title: "Portfolio Audit" },
  { id: "part_d_fundamental", title: "Fundamental" },
  { id: "part_d_technical", title: "Technical" },
  { id: "part_d_risk_caps", title: "Risk Caps" },
  { id: "part_e_journal", title: "Journal" },
  { id: "phase3_plan", title: "Monday Plan" },
];

const fmtDate = (iso: string) => (iso ? iso.slice(0, 10) : "—");

// History — past cycles, read-only. Closing a cycle keeps its file as a record;
// this is where you read it back. Richer than the journal row: it keeps every
// step's full output, including which candidates were struck and on what numbers.
export function HistoryPanel({ currentCycleId }: { currentCycleId?: string }) {
  const [rows, setRows] = useState<CycleSummary[] | null>(null);
  const [selected, setSelected] = useState<Cycle | null>(null);
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listCycles()
      .then(setRows)
      .catch((e) => setError(e instanceof ApiRequestError ? e.message : String(e)));
  }, []);

  async function open(cycleId: string) {
    if (selected?.cycle_id === cycleId) {
      setSelected(null);            // click the open row again to collapse
      return;
    }
    setLoadingId(cycleId);
    setError(null);
    try {
      setSelected(await api.getCycle(cycleId));
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setLoadingId(null);
    }
  }

  if (error) return <p className="error-box">{error}</p>;
  if (!rows) return <p className="muted">Loading history…</p>;

  return (
    <>
      <p className="panel-sub muted">
        Every cycle ever run, newest first. Closing a cycle keeps its file; deleting one
        removes it from here, though the journal entry it committed survives.
      </p>

      {rows.length === 0 && (
        <p className="stub-note">
          No cycles yet. Start a Friday ritual and it will appear here once it exists.
        </p>
      )}

      <div className="scan-table-wrap">
        <table className="scan-table">
          <thead>
            <tr>
              <th>Cycle</th><th>Status</th><th>Outcome</th>
              <th>Reached</th><th>Last updated</th><th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const isCurrent = c.cycle_id === currentCycleId;
              const isOpen = selected?.cycle_id === c.cycle_id;
              return (
                <tr key={c.cycle_id} className={isOpen ? "row-selected" : ""}>
                  <td>
                    <b>{c.cycle_id}</b>
                    {isCurrent && <span className="tag-keep hist-tag">active</span>}
                  </td>
                  <td><span className={`pill pill-${c.status}`}>{c.status}</span></td>
                  <td>
                    {c.outcome
                      ? <span className={c.outcome === "red_terminated" ? "tag-exit" : "tag-keep"}>
                          {c.outcome.replace(/_/g, " ")}
                        </span>
                      : <span className="muted">in progress</span>}
                  </td>
                  <td className="flags">{c.current_step.replace(/_/g, " ")}</td>
                  <td className="flags">{fmtDate(c.updated_at)}</td>
                  <td>
                    <button className="ghost" onClick={() => open(c.cycle_id)}
                            disabled={loadingId !== null}>
                      {loadingId === c.cycle_id ? "…" : isOpen ? "Hide" : "View"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {selected && <CycleDetail cycle={selected} />}
    </>
  );
}

function CycleDetail({ cycle }: { cycle: Cycle }) {
  const gate = cycle.steps.part_a_regime_gate.output;

  // Land on the most informative step that actually ran: the journal if it was
  // committed, otherwise the furthest step with output, otherwise the gate.
  const lastWithOutput = [...STEP_TITLES].reverse()
    .find((s) => (cycle.steps[s.id] as StepEnvelope<unknown>).output != null);
  const [stepId, setStepId] = useState<keyof CycleSteps>(
    cycle.steps.part_e_journal.output
      ? "part_e_journal"
      : lastWithOutput?.id ?? "part_a_regime_gate"
  );

  return (
    <div className="hist-detail">
      <div className="hist-head">
        <h4 className="jr-head">Cycle {cycle.cycle_id}</h4>
        {gate && (
          <span className="muted">
            <b className={gate.regime === "GREEN" ? "v-pass" : "v-strike"}>{gate.regime}</b>
            {" "}· {gate.filter_label} on {gate.index_label}
          </span>
        )}
      </div>

      {/* Same pills as before — now buttons, so any stage can be read back. */}
      <div className="hist-steps">
        {STEP_TITLES.map((s, i) => {
          const st = (cycle.steps[s.id] as StepEnvelope<unknown>).status as StepStatus;
          const active = stepId === s.id;
          return (
            <button key={s.id} onClick={() => setStepId(s.id)}
                    aria-current={active ? "step" : undefined}
                    title={`${s.title} — ${st}`}
                    className={`hist-step hist-step-${st}${active ? " hist-step-active" : ""}`}>
              <span className="hist-step-n">
                {st === "done" ? "✓" : st === "skipped" ? "–" : st === "failed" ? "!" : i + 1}
              </span>
              {s.title}
            </button>
          );
        })}
      </div>

      <div className="hist-step-body">
        <StepDetail cycle={cycle} stepId={stepId} />
      </div>
    </div>
  );
}
