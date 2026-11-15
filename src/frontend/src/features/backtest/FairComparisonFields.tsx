import type { BenchmarkOption } from "../../api/types";

// The two settings that decide whether a run answers the real question —
// "does this beat simply holding the index fund, after tax?" (Spec v2.3).

interface Props {
  benchmarks: BenchmarkOption[];
  benchmarkId: string | null;          // null = the regime index
  applyTax: boolean;
  onBenchmark: (id: string | null) => void;
  onApplyTax: (value: boolean) => void;
}

export function benchmarkOptionLabel(b: BenchmarkOption): string {
  const kind = b.total_return ? "total return" : "price only";
  const missing = b.available ? "" : b.source === "manual" ? " · file missing" : " · not cached";
  return `${b.label} — ${kind}${missing}`;
}

export function FairComparisonFields({
  benchmarks, benchmarkId, applyTax, onBenchmark, onApplyTax,
}: Props) {
  const chosen = benchmarks.find((b) => b.id === benchmarkId) ?? null;

  return (
    <div className="scan-filters">
      <label style={{ flex: 2 }}>
        <span>Benchmark</span>
        <select aria-label="Benchmark" value={benchmarkId ?? ""}
                onChange={(e) => onBenchmark(e.target.value || null)}>
          <option value="">Regime index (default)</option>
          {benchmarks.map((b) => (
            <option key={b.id} value={b.id}>{benchmarkOptionLabel(b)}</option>
          ))}
        </select>
        <span className="field-hint">
          {chosen
            ? chosen.note
            : "the regime index is usually a price index — it leaves out dividends, " +
              "which flatters the strategy. NIFTY MIDSMALLCAP 400 TRI is the fair comparison"}
        </span>
        {chosen && !chosen.available && chosen.how_to && (
          <span className="warn-box" role="alert">{chosen.how_to}</span>
        )}
      </label>

      <label className="check-label">
        <input type="checkbox" aria-label="Apply tax" checked={applyTax}
               onChange={(e) => onApplyTax(e.target.checked)} />
        <span>Apply capital-gains tax</span>
        <span className="field-hint">
          {applyTax
            ? "today's rates: short-term 20%, long-term 12.5% above ₹1.25 lakh"
            : "pre-tax — overstates what a high-turnover strategy keeps"}
        </span>
      </label>
    </div>
  );
}
