import { useEffect, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { SweepResult as SweepResultT, SweepVerdict } from "../../api/types";

// A sweep result is shown so its DIAGNOSTICS lead, not the winning CAGR. The
// bare number is the trap; the percentile, beat-rate and noise floor are what
// say whether selection actually found something.
export function SweepResult({ sweepId }: { sweepId: string }) {
  const [res, setRes] = useState<SweepResultT | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showGrid, setShowGrid] = useState(false);

  useEffect(() => {
    setRes(null);
    api.getSweepResult(sweepId)
      .then(setRes)
      .catch((e) => setError(e instanceof ApiRequestError ? e.message : String(e)));
  }, [sweepId]);

  if (error) return <p className="error-box">{error}</p>;
  if (!res) return <p className="muted">Loading sweep…</p>;

  const v = res.verdict;
  const n = res.noise;
  const trustworthy = interpret(v, n.spread);

  return (
    <div className="hist-detail">
      {/* The verdict comes first — a plain-language read of whether to believe it. */}
      <div className={`sweep-verdict sweep-verdict-${trustworthy.tone}`}>
        <b>{trustworthy.headline}</b>
        <p className="muted">{trustworthy.detail}</p>
      </div>

      <div className="port-stats">
        <Stat label="Winner (out-of-sample)" value={`${v.winner_test_cagr}%`}
              note={`vs baseline ${v.baseline_test_cagr}%`}
              good={v.winner_test_edge >= 0} />
        <Stat label="Test percentile"
              value={`${v.winner_test_percentile}th`}
              note={v.winner_test_percentile >= 90 ? "selection helped"
                : v.winner_test_percentile < 50 ? "below the median" : "mid-pack"}
              good={v.winner_test_percentile >= 90} />
        {/* This measures whether the BASELINE is weak, not whether the winner won.
            Only call it out (amber) when many beat it; otherwise stay neutral so
            it can't read as "the winner beat the baseline". */}
        <Stat label="% beating baseline" value={`${v.pct_beating_baseline_test}%`}
              note={v.pct_beating_baseline_test > 60 ? "baseline looks weak"
                : "of formulas tried"}
              tone={v.pct_beating_baseline_test > 60 ? "warn" : "neutral"} />
        <Stat label="Train→test corr" value={v.train_test_corr.toFixed(2)}
              note={v.train_test_corr >= 0.4 ? "choice transfers" : "weak transfer"}
              good={v.train_test_corr >= 0.4} />
      </div>

      <table className="scan-table sweep-summary-table">
        <tbody>
          <Row k="Winner formula (chosen on train)" v={<code>{v.winner_formula}</code>} />
          <Row k="Winner — train / test CAGR"
               v={`${v.winner_train_cagr}% / ${v.winner_test_cagr}%`} />
          <Row k="Winner edge — train / test"
               v={`${signed(v.winner_train_edge)}pp / ${signed(v.winner_test_edge)}pp`} />
          <Row k="Noise floor (random formulas, train)"
               v={`${n.min_cagr}% … ${n.max_cagr}% — spread ${n.spread}pp, sd ${n.std_cagr}pp`} />
          <Row k="Median formula (test)" v={`${v.median_test_cagr}%`} />
          <Row k="Best possible (hindsight, unattainable)" v={`${v.best_possible_test_cagr}%`} />
          <Row k="Formulas compared" v={String(v.formulas_tried)} />
        </tbody>
      </table>

      <div className="panel-actions">
        <button className="ghost" onClick={() => setShowGrid((s) => !s)}>
          {showGrid ? "Hide" : "Show"} all {res.grid.length} formulas
        </button>
      </div>

      {showGrid && (
        <div className="scan-table-wrap">
          <table className="scan-table">
            <thead>
              <tr>
                <th>Formula</th><th className="num">Train CAGR</th>
                <th className="num">Test CAGR</th><th className="num">Test Sharpe</th>
                <th className="num">Test maxDD</th><th className="num">Turnover</th>
              </tr>
            </thead>
            <tbody>
              {[...res.grid].sort((a, b) => b.train_cagr - a.train_cagr).map((r) => (
                <tr key={r.formula}
                    className={r.formula === v.winner_formula ? "row-selected" : ""}>
                  <td className="flags"><code>{r.formula}</code></td>
                  <td className="num">{r.train_cagr}%</td>
                  <td className="num">{r.test_cagr}%</td>
                  <td className="num">{r.test_sharpe}</td>
                  <td className="num">{r.test_maxdd}%</td>
                  <td className="num">{r.test_turnover}x</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h4 className="jr-head">Read this carefully</h4>
      <ul className="actions">{res.limitations.map((l, i) => <li key={i} className="muted">{l}</li>)}</ul>
    </div>
  );
}

// The plain-language verdict — so the numbers are interpreted, not just shown.
function interpret(v: SweepVerdict, noiseSpread: number):
    { tone: string; headline: string; detail: string } {
  // Most decisive first: the train-picked winner actually LOST to the baseline
  // out-of-sample, or landed below the median. Optimising here fit noise — a
  // negative edge is never "suggestive", and it must never read as "beats".
  if (v.winner_test_edge <= 0 || v.winner_test_percentile < 50) {
    return {
      tone: "bad",
      headline: "Optimisation did not help here — keep your current formula.",
      detail: `The train-picked winner ${v.winner_test_edge < 0 ? "UNDERPERFORMED" : "did not beat"} ` +
        `the baseline out-of-sample (${signed(v.winner_test_edge)}pp, ${v.winner_test_percentile}th ` +
        `percentile — below the median). With train→test correlation only ` +
        `${v.train_test_corr.toFixed(2)}, tuning the weights is fitting noise on this universe. ` +
        `Don't adopt the "winner"; the unoptimised formula is the safer choice.`,
    };
  }
  if (v.winner_test_percentile < 80 && v.pct_beating_baseline_test > 60) {
    return {
      tone: "warn",
      headline: "The baseline is weak here — selection barely helped.",
      detail: `${v.pct_beating_baseline_test}% of formulas beat the baseline out-of-sample, ` +
        `and the winner landed only at the ${v.winner_test_percentile}th percentile. ` +
        `That says the current formula is below-average for this universe, not that the ` +
        `search found something special. Almost any variant would improve on it.`,
    };
  }
  if (v.train_test_corr < 0.3) {
    return {
      tone: "bad",
      headline: "Formula choice does not transfer — treat any winner as noise.",
      detail: `Train and test CAGR correlate only ${v.train_test_corr.toFixed(2)} across ` +
        `${v.formulas_tried} formulas, and random formulas already span ${noiseSpread}pp. ` +
        `A winner picked on the training period tells you little about the future here.`,
    };
  }
  if (v.winner_test_percentile >= 90 && v.winner_test_edge > noiseSpread / 3) {
    return {
      tone: "good",
      headline: "A genuine, transferable improvement.",
      detail: `The winner reached the ${v.winner_test_percentile}th percentile out-of-sample ` +
        `with a ${signed(v.winner_test_edge)}pp edge, and formula choice transfers ` +
        `(corr ${v.train_test_corr.toFixed(2)}). Confirm it with a full backtest run before using it.`,
    };
  }
  // Reached only when the edge is positive (the <= 0 case returns above).
  return {
    tone: "warn",
    headline: "Suggestive, not conclusive.",
    detail: `The winner beats the baseline out-of-sample by +${v.winner_test_edge}pp, ` +
      `but read that against a noise floor of ${noiseSpread}pp and confirm with a real run.`,
  };
}

const signed = (n: number) => (n >= 0 ? `+${n}` : `${n}`);

function Stat({ label, value, note, good, tone }:
              { label: string; value: string; note: string;
                good?: boolean; tone?: "neutral" | "warn" }) {
  // `tone` wins when given (for metrics that are neither good nor bad, like the
  // baseline-weakness gauge); otherwise fall back to the good/bad pass/strike.
  const cls = tone === "warn" ? "v-strike" : tone === "neutral" ? ""
    : good ? "v-pass" : "v-strike";
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${cls}`}>{value}</span>
      <span className="stat-note">{note}</span>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <tr><td>{k}</td><td className="num">{v}</td></tr>;
}
