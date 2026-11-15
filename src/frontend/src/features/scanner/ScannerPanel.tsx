import { useEffect, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { Cycle, ScannerFilters, UniverseOption } from "../../api/types";
import { MultiSelect } from "../../components/MultiSelect";
import { ReopenStepButton } from "../../components/ReopenStepButton";
import { FROZEN_FORMULA, FrozenFormula } from "./FrozenFormula";

interface Props {
  cycle: Cycle;
  onCycleChange: (cycle: Cycle) => void;
}

const DEFAULT_UNIVERSES = ["midsmall400", "microcap250"];

// Part B — Stock Scoring: the Jerald Score, computed on correct 126-candle
// windows (no Excel). The formula is FROZEN since Spec v2.3 — it is shown, not
// edited, and the run never sends one (the backend would refuse it anyway).
export function ScannerPanel({ cycle, onCycleChange }: Props) {
  const step = cycle.steps.part_b_scanner;
  const out = step.output;
  const advanced = cycle.current_step !== "part_b_scanner";

  const today = new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local

  const [universes, setUniverses] = useState<UniverseOption[]>([]);
  const [sectors, setSectors] = useState<string[]>([]);
  // The Rulebook scans the two indices. Defaulting to "holdings" ranked the
  // portfolio against itself, where nothing can ever rank badly enough to exit.
  const [universeIds, setUniverseIds] = useState<string[]>(
    out?.universe_ids ?? DEFAULT_UNIVERSES);
  const [filters, setFilters] = useState<ScannerFilters>(() => {
    const base = out?.filters ?? { sectors: [], price_min: null, price_max: null, as_of: null };
    return { ...base, as_of: base.as_of ?? today }; // default the date to today
  });
  const [frozen, setFrozen] = useState<{ formula: string; reason?: string }>(
    { formula: FROZEN_FORMULA });
  const [busy, setBusy] = useState<null | "run" | "advance">(null);
  const [error, setError] = useState<string | null>(null);
  // The shown results become stale the moment any input changes; re-run to refresh.
  const [dirty, setDirty] = useState(false);
  const showResults = !!out && !dirty;

  useEffect(() => {
    api.getUniverseOptions().then(setUniverses);
    // Best-effort: the built-in copy is shown until (and unless) this answers.
    api.getFrozenFormula().then(setFrozen).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (universeIds.length === 0) {
      setSectors([]);
      return;
    }
    api.getUniverseSectors(universeIds).then(setSectors).catch(() => setSectors([]));
  }, [universeIds]);

  const numOrNull = (v: string): number | null => (v === "" ? null : Number(v));

  function changeUniverse(ids: string[]) {
    setUniverseIds(ids);
    setFilters((f) => ({ ...f, sectors: [] })); // sectors depend on the universe
    setDirty(true);
  }
  function changeFilters(patch: Partial<ScannerFilters>) {
    setFilters((f) => ({ ...f, ...patch }));
    setDirty(true);
  }
  async function run() {
    setBusy("run");
    setError(null);
    try {
      onCycleChange(await api.runPartB({ universe_ids: universeIds, filters }));
      setDirty(false);
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function advance() {
    setBusy("advance");
    setError(null);
    try {
      onCycleChange(await api.advancePartB());
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const inputsDisabled = advanced || busy !== null;

  return (
    <>
      <p className="panel-sub muted">
        Customize the filters to find the stocks that fit your goals. No Excel upload.
      </p>

      {/* Filters — row 1: universe / sector / date */}
      <div className="scan-filters">
        <label>
          <span>Index / universe (multi-select)</span>
          <MultiSelect
            options={universes.map((u) => ({ id: u.id, label: u.label }))}
            selected={universeIds}
            onChange={changeUniverse}
            disabled={inputsDisabled}
            placeholder="Select universes…"
          />
        </label>
        <label>
          <span>Sector (multi-select)</span>
          <MultiSelect
            options={sectors.map((s) => ({ id: s, label: s }))}
            selected={filters.sectors}
            onChange={(sel) => changeFilters({ sectors: sel })}
            disabled={inputsDisabled || sectors.length === 0}
            placeholder="All sectors"
          />
        </label>
        <label>
          <span>As-of date</span>
          <input type="date" value={filters.as_of ?? ""} disabled={inputsDisabled}
                 onChange={(e) => changeFilters({ as_of: e.target.value || null })} />
        </label>
      </div>

      {/* Filters — row 2: stock price range */}
      <div className="scan-filters">
        <label className="narrow">
          <span>Min price</span>
          <input type="number" value={filters.price_min ?? ""} disabled={inputsDisabled}
                 onChange={(e) => changeFilters({ price_min: numOrNull(e.target.value) })} />
        </label>
        <label className="narrow">
          <span>Max price</span>
          <input type="number" value={filters.price_max ?? ""} disabled={inputsDisabled}
                 onChange={(e) => changeFilters({ price_max: numOrNull(e.target.value) })} />
        </label>
      </div>

      {/* The score — read-only since Spec v2.3 */}
      <FrozenFormula formula={frozen.formula} reason={frozen.reason} />

      {/* Run — directly under the console */}
      <div className="panel-actions">
        <button onClick={run}
                disabled={busy !== null || advanced || universeIds.length === 0}>
          {busy === "run" ? "Scoring…" : out ? "Re-run" : "Run scoring"}
        </button>
        {advanced && (
          <>
            <span className="advanced-note">Advanced — see the next step below.</span>
            <ReopenStepButton stepId="part_b_scanner" onCycleChange={onCycleChange} />
          </>
        )}
      </div>

      {busy === "run" && (
        <p className="stale-hint">
          Scoring {universeIds.length} universe(s)… Large sets (400–650 names) can take a few
          minutes on the first run while prices are downloaded and cached. Inputs are locked
          until it finishes — subsequent runs are fast.
        </p>
      )}

      {step.status === "failed" && <p className="error-box">Failed: {step.error}</p>}
      {error && <p className="error-box">{error}</p>}
      {dirty && out && (
        <p className="stale-hint">Inputs changed — re-run to refresh the results.</p>
      )}

      {/* Results */}
      {showResults && out && (
        <div className="scan-results">
          <div className="scan-summary">
            {out.universe_label} · as of {out.as_of} · {out.eligible_count}/{out.total} eligible
          </div>
          <div className="scan-table-wrap">
            <table className="scan-table">
              <thead>
                <tr>
                  <th>#</th><th>Symbol</th><th>Sector</th>
                  <th className="num">Close</th><th className="num">Med TV (₹Cr)</th>
                  <th className="num">Score</th><th>Flags</th>
                </tr>
              </thead>
              <tbody>
                {out.candidates.map((c) => (
                  <tr key={c.symbol} className={c.eligible ? "" : "row-ineligible"}>
                    <td>{c.rank ?? "—"}</td>
                    <td>{c.symbol}</td>
                    <td>{c.sector}</td>
                    <td className="num">{c.close ?? "—"}</td>
                    <td className="num">{c.median_tv_cr ?? "—"}</td>
                    <td className="num">{c.score ?? "—"}</td>
                    <td className="flags">{c.flags.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {!advanced && (
            <div className="panel-actions">
              <button className="primary" onClick={advance} disabled={busy !== null}>
                Acknowledge &amp; proceed to Portfolio Audit
              </button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
