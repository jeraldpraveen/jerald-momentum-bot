// The live scoring formula, shown read-only.
//
// Spec v2.3 froze it: the walk-forward sweeps showed that a formula chosen on
// one period does not reliably win on the next, so editing it here would only
// invite fitting noise. Research alternatives live in the Backtest tab.

export const FROZEN_FORMULA =
  "((60% * 6 Month Performance) + (30% * 3 Month Performance) + (10% * 1 Month Performance)) " +
  "/ (3 Month Volatility + 1 Month Volatility)";

export const DEFAULT_REASON =
  "Frozen since Spec v2.3 — re-tuning the score against the backtest fits noise. " +
  "Try alternatives in the Backtest tab; adopting one is a Rulebook amendment.";

interface Props {
  formula?: string;
  reason?: string;
}

export function FrozenFormula({ formula, reason }: Props) {
  return (
    <div className="scan-console">
      <div className="scan-console-head">
        <span>Scoring formula</span>
        <span className="tag-keep">Frozen · Spec v2.3</span>
      </div>
      <pre className="scan-formula-input scan-formula-frozen" aria-label="Frozen scoring formula">
        {formula || FROZEN_FORMULA}
      </pre>
      <div className="scan-help">{reason || DEFAULT_REASON}</div>
    </div>
  );
}
