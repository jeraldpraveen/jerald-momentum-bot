import { useEffect, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { Cycle } from "../../api/types";
import { ReopenStepButton } from "../../components/ReopenStepButton";

interface Props {
  cycle: Cycle;
  onCycleChange: (cycle: Cycle) => void;
}

// Step-7 — Journal finalize. Compiles the week's row from every earlier step;
// the one manual field is "stops verified". Advancing appends it to the durable
// journal (journal.jsonl + journal.md), which outlives the cycle file.
export function JournalPanel({ cycle, onCycleChange }: Props) {
  const step = cycle.steps.part_e_journal;
  const out = step.output;
  const advanced = cycle.current_step !== "part_e_journal";

  const [stops, setStops] = useState<boolean | null>(out?.entry.stops_verified ?? null);
  const [notes, setNotes] = useState(out?.entry.notes ?? "");
  const [busy, setBusy] = useState<null | "run" | "advance">(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!out) return;
    setStops(out.entry.stops_verified);
    setNotes(out.entry.notes);
  }, [out?.entry.logged_at]);

  const pending = !!out &&
    (stops !== out.entry.stops_verified || notes !== out.entry.notes);

  async function act(kind: "run" | "advance") {
    setBusy(kind);
    setError(null);
    try {
      onCycleChange(kind === "run"
        ? await api.runJournal({ stops_verified: stops, notes })
        : await api.advanceJournal());
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const e = out?.entry;

  return (
    <>
      <p className="panel-sub muted">
        The week's permanent record: regime, holding ranks, exits, strike-outs with cited
        reasons, unfilled slots and stop verification. Advancing appends it to the journal
        file, which survives deleting the cycle.
      </p>

      <div className="scan-filters">
        <label>
          <span>Stops verified (GTTs in place)</span>
          <select value={stops === null ? "" : stops ? "yes" : "no"} disabled={advanced}
                  onChange={(ev) => setStops(ev.target.value === "" ? null : ev.target.value === "yes")}>
            <option value="">not confirmed</option>
            <option value="yes">Yes — all stops verified</option>
            <option value="no">No — stops missing</option>
          </select>
        </label>
        <label style={{ flex: 2 }}>
          <span>Notes</span>
          <input value={notes} disabled={advanced} placeholder="anything worth remembering…"
                 onChange={(ev) => setNotes(ev.target.value)} />
        </label>
      </div>

      <div className="panel-actions">
        <button className={pending ? "primary" : ""}
                onClick={() => act("run")} disabled={busy !== null || advanced}>
          {busy === "run" ? "Compiling…" : out ? "Re-compile" : "Compile journal entry"}
        </button>
        {advanced && (
          <>
            <span className="advanced-note">
              {out?.committed ? "Committed to the journal." : "Advanced — see the next step below."}
            </span>
            <ReopenStepButton stepId="part_e_journal" onCycleChange={onCycleChange} />
          </>
        )}
      </div>

      {step.status === "failed" && <p className="error-box">Failed: {step.error}</p>}
      {error && <p className="error-box">{error}</p>}
      {pending && !advanced && (
        <p className="stale-hint">Edited — click <b>Re-compile</b> to fold it into the entry.</p>
      )}

      {e && (
        <div className="scan-results">
          <div className="scan-summary">
            {e.date} · cycle {e.cycle_id} ·{" "}
            <span className={e.regime === "GREEN" ? "tag-keep" : "tag-exit"}>{e.regime}</span>{" "}
            {e.regime_detail} · portfolio {e.final_portfolio_size}
            {e.unfilled_slots > 0 && <> · {e.unfilled_slots} slot(s) in cash</>}
            {out?.committed && <> · <b className="v-pass">committed</b></>}
          </div>

          <div className="cap-grid">
            <div>
              <h4>Holdings <span className="muted">({e.holdings.length})</span></h4>
              <table className="scan-table">
                <thead><tr><th>Symbol</th><th className="num">Rank</th><th>Action</th><th>Why</th></tr></thead>
                <tbody>
                  {e.holdings.map((h) => (
                    <tr key={h.symbol} className={h.action === "EXIT" ? "row-exit" : ""}>
                      <td>{h.symbol}</td>
                      <td className="num">{h.rank ?? "∞"}</td>
                      <td>{h.action === "EXIT"
                        ? <span className="tag-exit">EXIT</span>
                        : <span className="tag-keep">KEEP</span>}</td>
                      <td className="flags">{h.reason}</td>
                    </tr>
                  ))}
                  {e.holdings.length === 0 && (
                    <tr><td colSpan={4} className="muted">No holdings.</td></tr>)}
                </tbody>
              </table>
            </div>

            <div>
              <h4>Buys <span className="muted">({e.buys.length})</span></h4>
              <table className="scan-table">
                <thead><tr><th>Symbol</th><th className="num">Rank</th><th>Sector</th><th className="num">Wt</th></tr></thead>
                <tbody>
                  {e.buys.map((b) => (
                    <tr key={b.symbol}>
                      <td><span className="tag-keep">{b.symbol}</span></td>
                      <td className="num">{b.rank}</td>
                      <td>{b.sector}</td>
                      <td className="num">~{b.weight_pct}%</td>
                    </tr>
                  ))}
                  {e.buys.length === 0 && (
                    <tr><td colSpan={4} className="muted">No buys this week.</td></tr>)}
                </tbody>
              </table>
            </div>
          </div>

          {e.strikeouts.length > 0 && (
            <>
              <h4 className="jr-head">Strike-outs <span className="muted">({e.strikeouts.length})</span></h4>
              <div className="scan-table-wrap">
                <table className="scan-table">
                  <thead><tr><th className="num">Rank</th><th>Symbol</th><th>Step</th><th>Cited reasons</th></tr></thead>
                  <tbody>
                    {e.strikeouts.map((s) => (
                      <tr key={`${s.step}-${s.symbol}`} className="row-ineligible">
                        <td className="num">{s.rank}</td>
                        <td>{s.symbol}</td>
                        <td>{s.step}</td>
                        <td className="flags">{s.reasons.join("; ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <ul className="actions">{out?.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>

          {!advanced && (
            <div className="panel-actions">
              <button className="primary" onClick={() => act("advance")}
                      disabled={busy !== null || pending}
                      title={pending ? "Re-compile your edits first" : undefined}>
                Commit journal &amp; proceed to Monday Plan
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
