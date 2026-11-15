import type { TaxBlock } from "../../api/types";

// After-tax result of a run, on equal terms: both the strategy and the
// benchmark are treated as sold in full on the last day (Spec v2.3). The
// headline CAGR above this is net of tax on what the strategy SOLD, but its
// open positions still carry untaxed gains and the benchmark has paid nothing.

const inr = (v: number | undefined) =>
  v == null ? "—" : `₹${Math.round(v).toLocaleString("en-IN")}`;

const signed = (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(2)}`;

interface Props {
  tax: TaxBlock | undefined;
  benchmarkLabel: string;
}

export function TaxSummary({ tax, benchmarkLabel }: Props) {
  if (!tax?.enabled || !tax.strategy || !tax.benchmark) {
    return (
      <p className="warn-box">
        <b>Pre-tax run.</b> Tax is not modelled here. The strategy turns over about six times a
        year, so nearly every gain is short-term — these figures overstate what you would keep.
        Re-run with “Apply capital-gains tax” for the comparison that matters.
      </p>
    );
  }

  const s = tax.strategy;
  const b = tax.benchmark;
  const edge = s.cagr_after_exit_pct - b.cagr_after_exit_pct;
  const years = tax.by_year ?? [];

  return (
    <div className="tax-summary">
      <h4 className="jr-head">After tax, both sold on the last day</h4>
      <div className="port-stats">
        <div className="stat">
          <span className="stat-label">Strategy CAGR after tax</span>
          <span className="stat-value">{s.cagr_after_exit_pct}%</span>
          <span className="stat-note">final {inr(s.end_equity_after_exit)}</span>
        </div>
        <div className="stat">
          <span className="stat-label">{benchmarkLabel} after tax</span>
          <span className="stat-value">{b.cagr_after_exit_pct}%</span>
          <span className="stat-note">exit tax {inr(b.exit_tax)}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Edge after tax</span>
          <span className={`stat-value ${edge >= 0 ? "v-pass" : "v-strike"}`}>
            {signed(edge)} pts
          </span>
          <span className="stat-note">
            {edge >= 0 ? "ahead of holding the benchmark" : "behind holding the benchmark"}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Tax paid during the run</span>
          <span className="stat-value">{inr(tax.total_tax_paid)}</span>
          <span className="stat-note">
            gains {inr(tax.capital_gains_tax)} · parked cash {inr(tax.cash_income_tax)}
          </span>
        </div>
      </div>

      {years.length > 0 && (
        <div className="scan-table-wrap">
          <table className="scan-table">
            <thead>
              <tr>
                <th>Financial year</th>
                <th className="num">Short-term gain</th>
                <th className="num">Long-term gain</th>
                <th className="num">Gains tax</th>
                <th className="num">Parked-cash tax</th>
              </tr>
            </thead>
            <tbody>
              {years.map((y) => (
                <tr key={y.fy}>
                  <td>{y.fy}</td>
                  <td className="num">{inr(y.short_term_gain)}</td>
                  <td className="num">{inr(y.long_term_gain)}</td>
                  <td className="num">{inr(y.capital_gains_tax)}</td>
                  <td className="num">{inr(y.cash_income_tax)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
