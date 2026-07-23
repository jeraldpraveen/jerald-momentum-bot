import { useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { CheckResult, Cycle } from "../../api/types";
import { ReopenStepButton } from "../../components/ReopenStepButton";

interface Props {
  cycle: Cycle;
  onCycleChange: (cycle: Cycle) => void;
}

function Verdict({ c }: { c: CheckResult }) {
  const cls = { pass: "v-pass", strike: "v-strike", unverified: "v-unver", "n/a": "v-na" }[c.verdict];
  return <span className={`verdict-tag ${cls}`} title={c.detail}>{c.verdict}</span>;
}

// Step-5 — Technical veto (Part D checks 3/4/5). Fully automatic: every value
// comes from cached price history, so there is nothing to enter.
export function TechnicalPanel({ cycle, onCycleChange }: Props) {
  const step = cycle.steps.part_d_technical;
  const out = step.output;
  const advanced = cycle.current_step !== "part_d_technical";
  const [busy, setBusy] = useState<null | "run" | "advance">(null);
  const [error, setError] = useState<string | null>(null);

  async function act(kind: "run" | "advance") {
    setBusy(kind);
    setError(null);
    try {
      onCycleChange(kind === "run" ? await api.runTechnical() : await api.advanceTechnical());
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <p className="panel-sub muted">
        Technical veto on the names that survived Step&nbsp;4. Fully automatic — every value is
        computed from price history, so there's nothing to fill in.
      </p>

      <div className="legend">
        <div><strong>The three checks</strong></div>
        <div>
          <b>Trend</b> — price below its 50-DMA → strike (not in an uptrend). &nbsp;
          <b>Extension</b> — more than 30% above its 20-DMA → strike (too stretched, poor entry).
          &nbsp;<b>RSI(14)</b> — above 82 → strike (overbought).
        </div>
      </div>

      <div className="panel-actions">
        <button onClick={() => act("run")} disabled={busy !== null || advanced}>
          {busy === "run" ? "Computing…" : out ? "Re-run" : "Run technical veto"}
        </button>
        {advanced && (
          <>
            <span className="advanced-note">Advanced — see the next step below.</span>
            <ReopenStepButton stepId="part_d_technical" onCycleChange={onCycleChange} />
          </>
        )}
      </div>

      {step.status === "failed" && <p className="error-box">Failed: {step.error}</p>}
      {error && <p className="error-box">{error}</p>}

      {out && (
        <div className="scan-results">
          <div className="scan-summary">
            as of {out.as_of} · {out.candidates.length} evaluated ·{" "}
            <strong>{out.struck_count} struck</strong>, {out.passed_count} surviving
          </div>

          <div className="scan-table-wrap">
            <table className="scan-table">
              <thead>
                <tr>
                  <th className="num">#</th><th>Symbol</th><th>Sector</th>
                  <th className="num">Close</th><th className="num">50-DMA</th>
                  <th className="num">20-DMA</th><th className="num">Ext %</th>
                  <th className="num">RSI(14)</th><th>Checks</th><th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {out.candidates.map((c) => (
                  <tr key={c.symbol} className={c.struck ? "row-exit" : ""}>
                    <td className="num">{c.rank}</td>
                    <td>{c.symbol}</td>
                    <td>{c.sector}</td>
                    <td className="num">{c.close ?? "—"}</td>
                    <td className="num">{c.dma_50 ?? "—"}</td>
                    <td className="num">{c.dma_20 ?? "—"}</td>
                    <td className="num">{c.extension_pct ?? "—"}</td>
                    <td className="num">{c.rsi_14 ?? "—"}</td>
                    <td className="checks-cell">
                      <Verdict c={c.trend} /><Verdict c={c.extension} /><Verdict c={c.overbought} />
                    </td>
                    <td>
                      {c.struck
                        ? <span className="tag-exit" title={c.strikes.join("; ")}>STRUCK</span>
                        : <span className="tag-keep" title={c.flags.join("; ") || "all checks cited"}>
                            PASS{c.flags.length ? ` (${c.flags.length}⚑)` : ""}
                          </span>}
                    </td>
                  </tr>
                ))}
                {out.candidates.length === 0 && (
                  <tr><td colSpan={10} className="muted">
                    No candidates survived Step 4 — nothing to check.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          <ul className="actions">{out.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>

          {!advanced && (
            <div className="panel-actions">
              <button className="primary" onClick={() => act("advance")} disabled={busy !== null}>
                Acknowledge &amp; proceed to Risk Caps
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
