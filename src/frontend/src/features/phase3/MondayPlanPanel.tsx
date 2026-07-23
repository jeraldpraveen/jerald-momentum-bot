import { useEffect, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { Cycle } from "../../api/types";
import { ReopenStepButton } from "../../components/ReopenStepButton";

interface Props {
  cycle: Cycle;
  onCycleChange: (cycle: Cycle) => void;
}

const inr = (n: number | null | undefined) =>
  n == null ? "—" : `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

// Step-8 — the Monday execution plan. Everything here is executed BY HAND at the
// open; this panel places no orders. Exits come first, one ticker at a time
// (cancel that GTT, then sell) — never a bulk GTT cancel.
export function MondayPlanPanel({ cycle, onCycleChange }: Props) {
  const step = cycle.steps.phase3_plan;
  const out = step.output;
  const done = cycle.current_step === "done";

  const [capital, setCapital] = useState<string>(
    out?.capital_source === "entered" && out.capital_basis ? String(out.capital_basis) : ""
  );
  const [busy, setBusy] = useState<null | "run" | "advance">(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (out?.capital_source === "entered" && out.capital_basis) setCapital(String(out.capital_basis));
  }, [out?.capital_basis, out?.capital_source]);

  const entered = capital.trim() === "" ? null : Number(capital);
  const pending = !!out &&
    (out.capital_source === "entered" ? entered !== out.capital_basis : entered !== null);

  async function act(kind: "run" | "advance") {
    setBusy(kind);
    setError(null);
    try {
      onCycleChange(kind === "run"
        ? await api.runPhase3({ portfolio_capital: entered })
        : await api.advancePhase3());
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <p className="panel-sub muted">
        Your printable checklist for Monday 09:15. Liquidations first — per ticker, cancel
        that position's GTT and then sell; never bulk-cancel, or you sit unprotected through
        the weekend gap. Then deploy into the buy list at ~10% each and place a GTT stop at
        <b> fill × 0.80</b> the same session. This tool places no orders.
      </p>

      <div className="scan-filters">
        <label>
          <span>Portfolio capital (₹)</span>
          <input type="number" min={0} step={1000} value={capital} disabled={done}
                 placeholder="blank = estimate from ledger"
                 onChange={(e) => setCapital(e.target.value)} />
        </label>
      </div>

      <div className="panel-actions">
        <button className={pending || !out ? "primary" : ""}
                onClick={() => act("run")} disabled={busy !== null || done}>
          {busy === "run" ? "Building…" : out ? "Rebuild plan" : "Build Monday plan"}
        </button>
        {done && (
          <>
            <span className="advanced-note">Cycle complete — the plan stays here for Monday.</span>
            <ReopenStepButton stepId="phase3_plan" onCycleChange={onCycleChange} />
          </>
        )}
      </div>

      {step.status === "failed" && <p className="error-box">Failed: {step.error}</p>}
      {error && <p className="error-box">{error}</p>}
      {pending && !done && (
        <p className="stale-hint">Capital changed — click <b>Rebuild plan</b> to re-size the buys.</p>
      )}

      {out && (
        <div className="scan-results">
          <div className="scan-summary">
            {out.as_of} ·{" "}
            <span className={out.regime === "GREEN" ? "tag-keep" : "tag-exit"}>{out.regime}</span>{" "}
            · {out.exits.length} exit(s), {out.buys.length} buy(s) · capital{" "}
            {inr(out.capital_basis)}{out.capital_source && <span className="muted"> ({out.capital_source})</span>}
            {out.est_proceeds != null && <> · proceeds ~{inr(out.est_proceeds)}</>}
            {out.est_deployment != null && <> · deploy ~{inr(out.est_deployment)}</>}
          </div>

          {out.warnings.map((w, i) => <p key={i} className="stale-hint">{w}</p>)}

          <h4 className="jr-head">Checklist</h4>
          <ol className="plan-checklist">
            {out.checklist.map((c, i) => (
              <li key={i} className={c.startsWith("   ") ? "plan-sub" : "plan-head"}>{c.trim()}</li>
            ))}
          </ol>

          {out.exits.length > 0 && (
            <>
              <h4 className="jr-head">
                Sell orders <span className="muted">({out.exits.length})</span>
              </h4>
              <div className="scan-table-wrap">
                <table className="scan-table">
                  <thead>
                    <tr>
                      <th>Symbol</th><th className="num">Qty</th><th className="num">Cancel GTT at</th>
                      <th className="num">Last close</th><th className="num">Est. proceeds</th><th>Why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {out.exits.map((e) => (
                      <tr key={e.symbol} className="row-exit">
                        <td><span className="tag-exit">{e.symbol}</span></td>
                        <td className="num">{e.qty ?? "—"}</td>
                        <td className="num">{e.existing_stop ?? "—"}</td>
                        <td className="num">{e.last_close ?? "—"}</td>
                        <td className="num">{inr(e.est_proceeds)}</td>
                        <td className="flags">{e.reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {out.buys.length > 0 && (
            <>
              <h4 className="jr-head">
                Buy orders <span className="muted">({out.buys.length})</span>
              </h4>
              <div className="scan-table-wrap">
                <table className="scan-table">
                  <thead>
                    <tr>
                      <th className="num">Rank</th><th>Symbol</th><th>Sector</th>
                      <th className="num">Weight</th><th className="num">Target ₹</th>
                      <th className="num">Last close</th><th className="num">~Qty</th>
                      <th className="num">Stop (fill × 0.80)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {out.buys.map((b) => (
                      <tr key={b.symbol}>
                        <td className="num">{b.rank}</td>
                        <td><span className="tag-keep">{b.symbol}</span></td>
                        <td>{b.sector}</td>
                        <td className="num">~{b.target_weight_pct}%</td>
                        <td className="num">{inr(b.target_value)}</td>
                        <td className="num">{b.last_close ?? "—"}</td>
                        <td className="num">{b.indicative_qty ?? "—"}</td>
                        <td className="num">{b.indicative_stop ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="stale-hint">
                Quantities and stops are indicative, computed off the last close. Size to your
                actual fill and set each GTT at the <b>real fill × 0.80</b>.
              </p>
            </>
          )}

          <ul className="actions">{out.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>

          {!done && (
            <div className="panel-actions">
              <button className="primary" onClick={() => act("advance")}
                      disabled={busy !== null || pending}
                      title={pending ? "Rebuild the plan first" : undefined}>
                {busy === "advance" ? "Finishing…" : "Acknowledge plan & complete cycle"}
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
