import { useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { Cycle } from "../../api/types";
import { ReopenStepButton } from "../../components/ReopenStepButton";

interface Props {
  cycle: Cycle;
  onCycleChange: (cycle: Cycle) => void;
}

const REASON: Record<string, string> = {
  sector_cap: "sector cap reached",
  microcap_cap: "microcap cap reached",
  slots_full: "slots already filled",
};

// Step-6 — Risk caps & slot filling. Walks Step-5 survivors in rank order and
// builds the buy list, skipping any name that breaches a concentration cap.
// Unfilled slots stay in cash — never force-filled.
export function RiskCapsPanel({ cycle, onCycleChange }: Props) {
  const step = cycle.steps.part_d_risk_caps;
  const out = step.output;
  const advanced = cycle.current_step !== "part_d_risk_caps";
  const [busy, setBusy] = useState<null | "run" | "advance">(null);
  const [error, setError] = useState<string | null>(null);

  async function act(kind: "run" | "advance") {
    setBusy(kind);
    setError(null);
    try {
      onCycleChange(kind === "run" ? await api.runRiskCaps() : await api.advanceRiskCaps());
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <p className="panel-sub muted">
        Fills your open slots from the Step-5 survivors, in strict rank order, subject to the
        concentration caps. Anything that would breach a cap is skipped and the next rank is
        tried. Slots that can't be filled stay in cash.
      </p>

      <div className="panel-actions">
        <button onClick={() => act("run")} disabled={busy !== null || advanced}>
          {busy === "run" ? "Building…" : out ? "Re-run" : "Build buy list"}
        </button>
        {advanced && (
          <>
            <span className="advanced-note">Advanced — see the next step below.</span>
            <ReopenStepButton stepId="part_d_risk_caps" onCycleChange={onCycleChange} />
          </>
        )}
      </div>

      {step.status === "failed" && <p className="error-box">Failed: {step.error}</p>}
      {error && <p className="error-box">{error}</p>}

      {out && (
        <div className="scan-results">
          <div className="scan-summary">
            keeping {out.kept.length} · {out.open_slots} open slot(s) ·{" "}
            <strong>{out.buys.length} to buy</strong>
            {out.unfilled_slots > 0 && <> · {out.unfilled_slots} unfilled (cash)</>} · resulting
            portfolio {out.final_portfolio_size} / {out.target_min}–{out.target_max} ·{" "}
            caps {out.max_per_sector}/sector, {out.max_microcap} microcap
          </div>

          <div className="cap-grid">
            <div>
              <h4>Buy list <span className="muted">({out.buys.length})</span></h4>
              {out.buys.length === 0 && <p className="muted">Nothing to buy — capital stays in cash.</p>}
              {out.buys.length > 0 && (
                <table className="scan-table">
                  <thead>
                    <tr><th className="num">#</th><th>Symbol</th><th>Sector</th>
                        <th>Sleeve</th><th className="num">Weight</th></tr>
                  </thead>
                  <tbody>
                    {out.buys.map((b) => (
                      <tr key={b.symbol}>
                        <td className="num">{b.rank}</td>
                        <td><span className="tag-keep">{b.symbol}</span></td>
                        <td>{b.sector}</td>
                        <td>{b.sleeve}</td>
                        <td className="num">~{b.weight_pct}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div>
              <h4>Skipped <span className="muted">({out.skipped.length})</span></h4>
              {out.skipped.length === 0 && <p className="muted">None skipped.</p>}
              {out.skipped.length > 0 && (
                <table className="scan-table">
                  <thead>
                    <tr><th className="num">#</th><th>Symbol</th><th>Sector</th><th>Why</th></tr>
                  </thead>
                  <tbody>
                    {out.skipped.map((s) => (
                      <tr key={s.symbol} className="row-ineligible">
                        <td className="num">{s.rank}</td>
                        <td>{s.symbol}</td>
                        <td>{s.sector}</td>
                        <td className="flags">{REASON[s.reason] ?? s.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          <div className="cap-counts">
            <b>Resulting concentration</b> —{" "}
            {Object.entries(out.sector_counts).length === 0
              ? "no positions"
              : Object.entries(out.sector_counts)
                  .map(([sec, n]) => `${sec}: ${n}`)
                  .join(" · ")}
            {" "}· microcap: {out.microcap_count}/{out.max_microcap}
          </div>

          <ul className="actions">{out.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>

          {!advanced && (
            <div className="panel-actions">
              <button className="primary" onClick={() => act("advance")} disabled={busy !== null}>
                Acknowledge &amp; proceed to Journal
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
