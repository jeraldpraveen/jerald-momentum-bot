import { useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { Cycle } from "../../api/types";
import { ReopenStepButton } from "../../components/ReopenStepButton";

interface Props {
  cycle: Cycle;
  onCycleChange: (cycle: Cycle) => void;
}

const REASON_LABEL: Record<string, string> = {
  rank_exit: "rank fell past threshold",
  dropped_from_universe: "no longer in universe",
};

// Part C — Portfolio Audit. Cross-references ACTIVE holdings against the Jerald
// Score ranking; flags rank>threshold or dropped names for Monday-open exit.
// Running is a preview; only advancing writes PENDING_EXIT to the ledger.
export function PortfolioAuditPanel({ cycle, onCycleChange }: Props) {
  const step = cycle.steps.part_c_exit_flags;
  const out = step.output;
  const advanced = cycle.current_step !== "part_c_exit_flags";
  const [busy, setBusy] = useState<null | "run" | "advance">(null);
  const [error, setError] = useState<string | null>(null);

  async function act(kind: "run" | "advance") {
    setBusy(kind);
    setError(null);
    try {
      onCycleChange(kind === "run" ? await api.runPartC() : await api.advancePartC());
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <p className="panel-sub muted">
        Cross-check your holdings against the Jerald Score ranking. Holdings ranked past the
        threshold, or no longer in the scanned universe, are flagged to sell at Monday open.
      </p>

      <div className="panel-actions">
        <button onClick={() => act("run")} disabled={busy !== null || advanced}>
          {busy === "run" ? "Auditing…" : out ? "Re-run audit" : "Run portfolio audit"}
        </button>
        {advanced && (
          <>
            <span className="advanced-note">Advanced — see the next step below.</span>
            <ReopenStepButton stepId="part_c_exit_flags" onCycleChange={onCycleChange} />
          </>
        )}
      </div>

      {step.status === "failed" && <p className="error-box">Failed: {step.error}</p>}
      {error && <p className="error-box">{error}</p>}

      {out && (
        <div className="scan-results">
          <div className="scan-summary">
            vs {out.universe_label} · as of {out.as_of} · rank exit &gt; {out.rank_threshold} ·{" "}
            <strong>{out.exit_count} to exit</strong>, {out.keep_count} to keep
          </div>

          {out.holdings.length === 0 && (
            <p className="stub-note">
              No ACTIVE holdings to audit. Every position in the ledger is already marked
              PENDING_EXIT (or the ledger is empty), so there is nothing to rank-check.
            </p>
          )}

          {out.holdings.length > 0 && (
          <div className="scan-table-wrap">
            <table className="scan-table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Sector</th>
                  <th className="num">Rank</th><th className="num">Score</th>
                  <th>Action</th><th>Why</th>
                </tr>
              </thead>
              <tbody>
                {out.holdings.map((h) => (
                  <tr key={h.symbol} className={h.exit_flagged ? "row-exit" : ""}>
                    <td>{h.symbol}</td>
                    <td>{h.sector}</td>
                    <td className="num">{h.rank ?? "∞"}</td>
                    <td className="num">{h.score ?? "—"}</td>
                    <td>{h.exit_flagged ? <span className="tag-exit">EXIT</span>
                                        : <span className="tag-keep">KEEP</span>}</td>
                    <td className="flags">
                      {h.reasons.map((r) => REASON_LABEL[r] ?? r).join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          )}

          <ul className="actions">
            {out.actions.map((a, i) => <li key={i}>{a}</li>)}
          </ul>

          {!advanced && (
            <div className="panel-actions">
              <button className="primary" onClick={() => act("advance")} disabled={busy !== null}>
                {out.exit_count > 0
                  ? `Acknowledge & mark ${out.exit_count} exit(s)`
                  : "Acknowledge & proceed"}
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
