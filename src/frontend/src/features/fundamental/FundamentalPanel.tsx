import { useEffect, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { CheckResult, Cycle, FundamentalAssessment } from "../../api/types";
import { ReopenStepButton } from "../../components/ReopenStepButton";

interface Props {
  cycle: Cycle;
  onCycleChange: (cycle: Cycle) => void;
}

const BLANK: FundamentalAssessment = {
  pledging_pct: null, interest_coverage: null, pending_action: null, note: "",
};

// Research links. Moneycontrol's search URL redirects to the company page and
// charts "Pledges as % of promoter shares" directly — the clearest pledging source.
// Screener's main company page carries BOTH checks: the Cons box (Ctrl+F "pled")
// and the Announcements panel (Important tab) for pending corporate/SEBI actions.
const mcUrl = (s: string) =>
  `https://www.moneycontrol.com/stocks/cptmarket/compsearchnew.php?search_str=${encodeURIComponent(s)}`;
const screenerUrl = (s: string) =>
  `https://www.screener.in/company/${encodeURIComponent(s)}/consolidated/`;

function Verdict({ c }: { c: CheckResult }) {
  const cls = { pass: "v-pass", strike: "v-strike", unverified: "v-unver", "n/a": "v-na" }[c.verdict];
  return <span className={`verdict-tag ${cls}`} title={c.detail}>{c.verdict}</span>;
}

// Step-4 — Fundamental veto (Part D checks 1/2/6/7), eliminate-only.
// Interest coverage is auto-fetched and pre-filled; every value stays editable
// so you can verify and correct it. Blank = "unverified" -> passes with a flag.
export function FundamentalPanel({ cycle, onCycleChange }: Props) {
  const step = cycle.steps.part_d_fundamental;
  const out = step.output;
  const advanced = cycle.current_step !== "part_d_fundamental";
  const [edits, setEdits] = useState<Record<string, FundamentalAssessment>>({});
  const [busy, setBusy] = useState<null | "run" | "advance">(null);
  const [error, setError] = useState<string | null>(null);

  // Seed the editable cells from whatever the last run stored.
  useEffect(() => {
    if (!out) return;
    const seed: Record<string, FundamentalAssessment> = {};
    out.candidates.forEach((c) => { seed[c.symbol] = { ...BLANK, ...c.assessment }; });
    setEdits(seed);
  }, [out?.as_of, out?.candidates.length]);

  const get = (sym: string) => edits[sym] ?? BLANK;
  const set = (sym: string, p: Partial<FundamentalAssessment>) =>
    setEdits((e) => ({ ...e, [sym]: { ...get(sym), ...p } }));
  const numOrNull = (v: string) => (v === "" ? null : Number(v));

  // Verdicts come from the last evaluation — flag when the table has edits that
  // haven't been applied yet, so a typed value never looks silently ignored.
  const pendingEdits = !!out && out.candidates.some((c) => {
    const a = get(c.symbol), b = c.assessment;
    return a.pledging_pct !== b.pledging_pct
      || a.interest_coverage !== b.interest_coverage
      || a.pending_action !== b.pending_action
      || (a.note || "") !== (b.note || "");
  });

  async function act(kind: "run" | "advance") {
    setBusy(kind);
    setError(null);
    try {
      onCycleChange(kind === "run" ? await api.runFundamental(edits) : await api.advanceFundamental());
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <p className="panel-sub muted">
        Eliminate-only veto on ranks 1–{out?.rank_window ?? 15} you don't already hold.
        Fill in what you can verify; leave the rest blank.
      </p>

      <div className="legend">
        <div><strong>How to read this</strong></div>
        <div>
          <span className="verdict-tag v-pass">pass</span> check evaluated, candidate is fine ·{" "}
          <span className="verdict-tag v-strike">strike</span> fails the rule → eliminated ·{" "}
          <span className="verdict-tag v-unver">unverified</span> nothing entered yet — <em>passes
          anyway</em>, just flagged ·{" "}
          <span className="verdict-tag v-na">n/a</span> rule doesn't apply (financials)
        </div>
        <div className="legend-cols">
          <b>MC</b> = moneycontrol (charts pledging directly). <b>SCR</b> = screener company page —
          one stop for <i>both</i> manual checks: Cons box (Ctrl+F "pled") and Announcements →
          <i>Important</i> tab. &nbsp;<b>Pledging %</b> — % of promoter shares pledged as loan
          collateral; &gt; 20% strikes. &nbsp;<b>Int. cover</b> — interest-coverage ratio, EBIT ÷ interest
          expense: how many times operating profit covers the interest bill. Auto-fetched;
          type only to override. &lt; 2 strikes (debt distress); n/a for financials.
          &nbsp;<b>Pending action</b> — NSE ASM/GSM surveillance is checked automatically and
          strikes on its own; mark <i>none</i> only after you've also glanced at announcements
          (mergers, SEBI cases) via the SCR link. &nbsp;<b>Listed</b> — auto from price history.
        </div>
      </div>

      {/* Before the first run the action lives here; once the table exists the
          Re-evaluate button sits below it, next to where you're typing. */}
      {(!out || advanced) && (
        <div className="panel-actions">
          {!out && (
            <button onClick={() => act("run")} disabled={busy !== null || advanced}>
              {busy === "run" ? "Evaluating…" : "Run fundamental veto"}
            </button>
          )}
          {advanced && (
            <>
              <span className="advanced-note">Advanced — see the next step below.</span>
              <ReopenStepButton stepId="part_d_fundamental" onCycleChange={onCycleChange} />
            </>
          )}
        </div>
      )}

      {step.status === "failed" && <p className="error-box">Failed: {step.error}</p>}
      {error && <p className="error-box">{error}</p>}
      {pendingEdits && !advanced && (
        <p className="stale-hint">
          You've edited values — the verdicts below still show the previous evaluation.
          Click <b>Re-evaluate</b> to apply them.
        </p>
      )}

      {out && (
        <div className="scan-results">
          <div className="scan-summary">
            as of {out.as_of} · ranks 1–{out.rank_window} · {out.candidates.length} candidate(s) ·{" "}
            <strong>{out.struck_count} struck</strong>, {out.passed_count} surviving
          </div>

          <div className="scan-table-wrap">
            <table className="scan-table port-table">
              <thead>
                <tr>
                  <th className="num">#</th><th>Symbol</th><th>Sector</th>
                  <th className="num">Pledging % <span className="th-hint">you enter</span></th>
                  <th className="num">Int. cover <span className="th-hint">auto</span></th>
                  <th>Pending action <span className="th-hint">ASM/GSM auto + you</span></th>
                  <th>Listed <span className="th-hint">auto</span></th>
                  <th>Note / citation</th><th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {out.candidates.map((c) => {
                  const a = get(c.symbol);
                  return (
                    <tr key={c.symbol} className={c.struck ? "row-exit" : ""}>
                      <td className="num">{c.rank}</td>
                      <td>
                        <div className="sym-cell">
                          <span>{c.symbol}</span>
                          <span className="sym-links">
                            <a href={mcUrl(c.symbol)} target="_blank" rel="noopener noreferrer"
                               title="Moneycontrol — shareholding chart with 'Pledges as % of promoter shares'">MC</a>
                            <a href={screenerUrl(c.symbol)} target="_blank" rel="noopener noreferrer"
                               title="Screener.in company page — Cons box for pledging (Ctrl+F 'pled') AND Announcements → Important tab for pending actions">SCR</a>
                          </span>
                        </div>
                      </td>
                      <td>{c.sector}</td>
                      <td className="num">
                        <input className="cell w-num" type="number" step="any" placeholder="e.g. 12.5"
                               value={a.pledging_pct ?? ""} disabled={advanced}
                               onChange={(e) => set(c.symbol, { pledging_pct: numOrNull(e.target.value) })} />
                        <Verdict c={c.pledging} />
                      </td>
                      <td className="num">
                        <input className="cell w-num" type="number" step="any"
                               disabled={advanced || c.is_financial}
                               placeholder={c.auto_interest_coverage?.toString() ?? "—"}
                               value={a.interest_coverage ?? ""}
                               onChange={(e) => set(c.symbol, { interest_coverage: numOrNull(e.target.value) })} />
                        <Verdict c={c.interest_cover} />
                      </td>
                      <td>
                        {c.surveillance
                          ? <span className="surv-badge" title="NSE surveillance — automatic strike">
                              {c.surveillance}
                            </span>
                          : <select className="cell w-status" value={
                                      a.pending_action === null ? "" : a.pending_action ? "yes" : "no"}
                                    disabled={advanced}
                                    onChange={(e) => set(c.symbol, {
                                      pending_action: e.target.value === "" ? null : e.target.value === "yes",
                                    })}>
                              <option value="">unchecked</option>
                              <option value="no">none</option>
                              <option value="yes">pending</option>
                            </select>}
                        <Verdict c={c.pending_action} />
                      </td>
                      <td>
                        {c.listed_days}d <Verdict c={c.listing_age} />
                      </td>
                      <td>
                        <input className="cell w-txt" value={a.note} disabled={advanced}
                               placeholder="source…"
                               onChange={(e) => set(c.symbol, { note: e.target.value })} />
                      </td>
                      <td>
                        {c.struck
                          ? <span className="tag-exit" title={c.strikes.join("; ")}>STRUCK</span>
                          : <span className="tag-keep" title={c.flags.join("; ") || "all checks cited"}>
                              PASS{c.flags.length ? ` (${c.flags.length}⚑)` : ""}
                            </span>}
                      </td>
                    </tr>
                  );
                })}
                {out.candidates.length === 0 && (
                  <tr><td colSpan={9} className="muted">
                    No candidates — every top-{out.rank_window} name is already held.
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          <ul className="actions">{out.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>

          {!advanced && (
            <div className="panel-actions">
              <button className={pendingEdits ? "primary" : ""}
                      onClick={() => act("run")} disabled={busy !== null}>
                {busy === "run" ? "Evaluating…" : "Re-evaluate"}
              </button>
              <button className={pendingEdits ? "" : "primary"}
                      onClick={() => act("advance")} disabled={busy !== null || pendingEdits}
                      title={pendingEdits ? "Apply your edits first" : undefined}>
                Acknowledge &amp; proceed to Technical Analysis
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
