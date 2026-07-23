import type { Cycle, CycleSteps } from "../../api/types";

const pct = (n: number | null) => (n == null ? "—" : `${n}%`);
const num = (n: number | null | undefined, dp = 2) =>
  n == null ? "—" : n.toLocaleString("en-IN", { maximumFractionDigits: dp });

// Read-only renderings of each step's stored output, for the History view.
// Deliberately compact — this is for reading back a past decision, not editing
// it, so every control and re-run button is gone.
export function StepDetail({ cycle, stepId }: { cycle: Cycle; stepId: keyof CycleSteps }) {
  const env = cycle.steps[stepId];

  if (env.status === "skipped") {
    return <p className="stub-note">Skipped — the RED regime terminated this week early.</p>;
  }
  if (env.status === "failed") {
    return <p className="error-box">This step failed: {env.error}</p>;
  }
  if (!env.output) {
    return <p className="stub-note">Never run — the cycle ended before reaching this step.</p>;
  }

  switch (stepId) {
    case "part_a_regime_gate": {
      const o = cycle.steps.part_a_regime_gate.output!;
      return (
        <>
          <div className={`verdict verdict-${o.regime === "GREEN" ? "green" : "red"}`}>
            <div className="verdict-flag">{o.regime}</div>
            <div className="muted">
              {o.filter_label} on {o.index_label} · week ending {o.week_end}
            </div>
          </div>
          <dl className="metrics">
            <div><dt>Index close</dt><dd>{num(o.index_close)}</dd></div>
            <div><dt>{o.line_label}</dt><dd>{num(o.line_value)}</dd></div>
            <div><dt>Ticker</dt><dd className="src">{o.index_ticker}</dd></div>
            <div><dt>Re-entry</dt><dd>{o.reentry_required ? "required" : "no"}</dd></div>
          </dl>
          <ul className="actions">{o.actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </>
      );
    }

    case "part_b_scanner": {
      const o = cycle.steps.part_b_scanner.output!;
      const top = o.candidates.slice(0, 25);
      return (
        <>
          <div className="scan-summary">
            {o.universe_label} · as of {o.as_of} · {o.eligible_count} eligible of {o.total}
          </div>
          <p className="verdict-def"><code>{o.formula}</code></p>
          <div className="scan-table-wrap">
            <table className="scan-table">
              <thead>
                <tr>
                  <th className="num">#</th><th>Symbol</th><th>Sector</th>
                  <th className="num">Score</th><th className="num">Close</th>
                  <th className="num">Median TV</th><th>Flags</th>
                </tr>
              </thead>
              <tbody>
                {top.map((c) => (
                  <tr key={c.symbol} className={c.eligible ? "" : "row-ineligible"}>
                    <td className="num">{c.rank ?? "—"}</td>
                    <td>{c.symbol}</td>
                    <td>{c.sector}</td>
                    <td className="num">{num(c.score, 3)}</td>
                    <td className="num">{num(c.close)}</td>
                    <td className="num">{num(c.median_tv_cr)} Cr</td>
                    <td className="flags">{c.flags.join("; ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {o.candidates.length > top.length && (
            <p className="stale-hint">
              Showing the top {top.length} of {o.candidates.length} scored names.
            </p>
          )}
        </>
      );
    }

    case "part_c_exit_flags": {
      const o = cycle.steps.part_c_exit_flags.output!;
      return (
        <>
          <div className="scan-summary">
            as of {o.as_of} · rank threshold {o.rank_threshold} · {o.exit_count} to exit,{" "}
            {o.keep_count} to keep
          </div>
          <table className="scan-table">
            <thead>
              <tr>
                <th>Symbol</th><th>Sector</th><th className="num">Rank</th>
                <th className="num">Score</th><th>In universe</th><th>Verdict</th><th>Why</th>
              </tr>
            </thead>
            <tbody>
              {o.holdings.map((h) => (
                <tr key={h.symbol} className={h.exit_flagged ? "row-exit" : ""}>
                  <td>{h.symbol}</td>
                  <td>{h.sector}</td>
                  <td className="num">{h.rank ?? "∞"}</td>
                  <td className="num">{num(h.score, 3)}</td>
                  <td>{h.in_universe ? "yes" : "no"}</td>
                  <td>{h.exit_flagged
                    ? <span className="tag-exit">EXIT</span>
                    : <span className="tag-keep">KEEP</span>}</td>
                  <td className="flags">{h.reasons.join("; ")}</td>
                </tr>
              ))}
              {o.holdings.length === 0 && (
                <tr><td colSpan={7} className="muted">No holdings audited.</td></tr>)}
            </tbody>
          </table>
        </>
      );
    }

    case "part_d_fundamental": {
      const o = cycle.steps.part_d_fundamental.output!;
      return (
        <>
          <div className="scan-summary">
            as of {o.as_of} · ranks 1–{o.rank_window} · {o.struck_count} struck,{" "}
            {o.passed_count} passed
          </div>
          <div className="scan-table-wrap">
            <table className="scan-table">
              <thead>
                <tr>
                  <th className="num">#</th><th>Symbol</th><th>Pledging</th><th>Int. cover</th>
                  <th>Pending action</th><th>Listing age</th><th>Verdict</th>
                </tr>
              </thead>
              <tbody>
                {o.candidates.map((c) => (
                  <tr key={c.symbol} className={c.struck ? "row-ineligible" : ""}>
                    <td className="num">{c.rank}</td>
                    <td>{c.symbol}</td>
                    <td className="flags">{c.pledging.detail}</td>
                    <td className="flags">{c.interest_cover.detail}</td>
                    <td className="flags">{c.pending_action.detail}</td>
                    <td className="flags">{c.listing_age.detail}</td>
                    <td>{c.struck
                      ? <span className="tag-exit">STRUCK</span>
                      : <span className="tag-keep">PASS</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      );
    }

    case "part_d_technical": {
      const o = cycle.steps.part_d_technical.output!;
      return (
        <>
          <div className="scan-summary">
            as of {o.as_of} · {o.struck_count} struck, {o.passed_count} passed
          </div>
          <div className="scan-table-wrap">
            <table className="scan-table">
              <thead>
                <tr>
                  <th className="num">#</th><th>Symbol</th><th className="num">Close</th>
                  <th className="num">50-DMA</th><th className="num">20-DMA</th>
                  <th className="num">Extension</th><th className="num">RSI(14)</th>
                  <th>Verdict</th><th>Strikes</th>
                </tr>
              </thead>
              <tbody>
                {o.candidates.map((c) => (
                  <tr key={c.symbol} className={c.struck ? "row-ineligible" : ""}>
                    <td className="num">{c.rank}</td>
                    <td>{c.symbol}</td>
                    <td className="num">{num(c.close)}</td>
                    <td className="num">{num(c.dma_50)}</td>
                    <td className="num">{num(c.dma_20)}</td>
                    <td className="num">{pct(c.extension_pct)}</td>
                    <td className="num">{num(c.rsi_14, 1)}</td>
                    <td>{c.struck
                      ? <span className="tag-exit">STRUCK</span>
                      : <span className="tag-keep">PASS</span>}</td>
                    <td className="flags">{c.strikes.join("; ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      );
    }

    case "part_d_risk_caps": {
      const o = cycle.steps.part_d_risk_caps.output!;
      return (
        <>
          <div className="scan-summary">
            as of {o.as_of} · portfolio {o.final_portfolio_size} (target {o.target_min}–
            {o.target_max}) · {o.open_slots} open slot(s) · {o.unfilled_slots} left in cash ·
            max {o.max_per_sector}/sector, {o.max_microcap} microcap
          </div>
          <div className="cap-grid">
            <div>
              <h4>Bought <span className="muted">({o.buys.length})</span></h4>
              <table className="scan-table">
                <thead><tr><th className="num">#</th><th>Symbol</th><th>Sector</th><th className="num">Wt</th></tr></thead>
                <tbody>
                  {o.buys.map((b) => (
                    <tr key={b.symbol}>
                      <td className="num">{b.rank}</td>
                      <td><span className="tag-keep">{b.symbol}</span></td>
                      <td>{b.sector}</td>
                      <td className="num">~{b.weight_pct}%</td>
                    </tr>
                  ))}
                  {o.buys.length === 0 && (
                    <tr><td colSpan={4} className="muted">No buys.</td></tr>)}
                </tbody>
              </table>
            </div>
            <div>
              <h4>Kept <span className="muted">({o.kept.length})</span></h4>
              <table className="scan-table">
                <thead><tr><th>Symbol</th><th>Sector</th><th>Sleeve</th></tr></thead>
                <tbody>
                  {o.kept.map((k) => (
                    <tr key={k.symbol}><td>{k.symbol}</td><td>{k.sector}</td><td>{k.sleeve}</td></tr>
                  ))}
                  {o.kept.length === 0 && (
                    <tr><td colSpan={3} className="muted">Nothing kept.</td></tr>)}
                </tbody>
              </table>
            </div>
          </div>
          {o.skipped.length > 0 && (
            <>
              <h4 className="jr-head">Skipped by a cap <span className="muted">({o.skipped.length})</span></h4>
              <table className="scan-table">
                <thead><tr><th className="num">#</th><th>Symbol</th><th>Sector</th><th>Reason</th></tr></thead>
                <tbody>
                  {o.skipped.map((s) => (
                    <tr key={s.symbol} className="row-ineligible">
                      <td className="num">{s.rank}</td>
                      <td>{s.symbol}</td>
                      <td>{s.sector}</td>
                      <td className="flags">{s.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      );
    }

    case "part_e_journal": {
      const e = cycle.steps.part_e_journal.output!.entry;
      return (
        <>
          <div className="scan-summary">
            {e.date} · <span className={e.regime === "GREEN" ? "tag-keep" : "tag-exit"}>{e.regime}</span>{" "}
            {e.regime_detail} · portfolio {e.final_portfolio_size}
            {e.unfilled_slots > 0 && <> · {e.unfilled_slots} slot(s) in cash</>}
            {" · stops "}
            {e.stops_verified === true ? "verified"
              : e.stops_verified === false ? "NOT verified" : "not confirmed"}
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
                    <tr><td colSpan={4} className="muted">None.</td></tr>)}
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
                    <tr><td colSpan={4} className="muted">No buys.</td></tr>)}
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
          {e.notes && <p className="verdict-def">Notes: {e.notes}</p>}
        </>
      );
    }

    case "phase3_plan": {
      const o = cycle.steps.phase3_plan.output!;
      return (
        <>
          <div className="scan-summary">
            {o.as_of} · <span className={o.regime === "GREEN" ? "tag-keep" : "tag-exit"}>{o.regime}</span>{" "}
            · {o.exits.length} exit(s), {o.buys.length} buy(s)
            {o.capital_basis != null && <> · capital ₹{num(o.capital_basis, 0)} ({o.capital_source})</>}
          </div>
          <h4 className="jr-head">Checklist</h4>
          <ol className="plan-checklist">
            {o.checklist.map((c, i) => (
              <li key={i} className={c.startsWith("   ") ? "plan-sub" : "plan-head"}>{c.trim()}</li>
            ))}
          </ol>
          {o.exits.length > 0 && (
            <>
              <h4 className="jr-head">Sell orders</h4>
              <table className="scan-table">
                <thead><tr><th>Symbol</th><th className="num">Qty</th><th className="num">GTT</th><th>Why</th></tr></thead>
                <tbody>
                  {o.exits.map((e) => (
                    <tr key={e.symbol} className="row-exit">
                      <td><span className="tag-exit">{e.symbol}</span></td>
                      <td className="num">{e.qty ?? "—"}</td>
                      <td className="num">{num(e.existing_stop)}</td>
                      <td className="flags">{e.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
          {o.buys.length > 0 && (
            <>
              <h4 className="jr-head">Buy orders</h4>
              <table className="scan-table">
                <thead>
                  <tr>
                    <th className="num">#</th><th>Symbol</th><th className="num">~Qty</th>
                    <th className="num">Target ₹</th><th className="num">Stop</th>
                  </tr>
                </thead>
                <tbody>
                  {o.buys.map((b) => (
                    <tr key={b.symbol}>
                      <td className="num">{b.rank}</td>
                      <td><span className="tag-keep">{b.symbol}</span></td>
                      <td className="num">{b.indicative_qty ?? "—"}</td>
                      <td className="num">{num(b.target_value, 0)}</td>
                      <td className="num">{num(b.indicative_stop)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </>
      );
    }

    default:
      return null;
  }
}
