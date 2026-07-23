import { useEffect, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { Cycle, ScannerFilters, UniverseOption } from "../../api/types";
import { MultiSelect } from "../../components/MultiSelect";
import { ReopenStepButton } from "../../components/ReopenStepButton";

interface Props {
  cycle: Cycle;
  onCycleChange: (cycle: Cycle) => void;
}

const DEFAULT_FORMULA =
  "((60% * 6 Month Performance) + (30% * 3 Month Performance) + (10% * 1 Month Performance)) " +
  "/ (3 Month Volatility + 1 Month Volatility)";

const TERM_HELP =
  "Terms: 1/3/6/9 Month & 1 Year  ×  Performance | Volatility.  " +
  "Operators: + - × ÷ ( ).  Weights in %.";

// Part B — Stock Scoring: the Jerald Score. Formula console modeled on the
// Sigma Scanner vocabulary but computed on correct 126-candle windows (no Excel).
export function ScannerPanel({ cycle, onCycleChange }: Props) {
  const step = cycle.steps.part_b_scanner;
  const out = step.output;
  const advanced = cycle.current_step !== "part_b_scanner";

  const today = new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local

  const [universes, setUniverses] = useState<UniverseOption[]>([]);
  const [sectors, setSectors] = useState<string[]>([]);
  const [universeIds, setUniverseIds] = useState<string[]>(out?.universe_ids ?? ["holdings"]);
  const [filters, setFilters] = useState<ScannerFilters>(() => {
    const base = out?.filters ?? { sectors: [], price_min: null, price_max: null, as_of: null };
    return { ...base, as_of: base.as_of ?? today }; // default the date to today
  });
  const [formula, setFormula] = useState<string>(out?.formula ?? DEFAULT_FORMULA);
  const [formulaError, setFormulaError] = useState<string | null>(null);
  const [busy, setBusy] = useState<null | "run" | "advance">(null);
  const [error, setError] = useState<string | null>(null);
  // The shown results become stale the moment any input changes; re-run to refresh.
  const [dirty, setDirty] = useState(false);
  const showResults = !!out && !dirty;

  useEffect(() => {
    api.getUniverseOptions().then(setUniverses);
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
  function changeFormula(next: string) {
    setFormula(next);
    setFormulaError(null);
    setDirty(true);
  }

  async function checkFormula() {
    try {
      const res = await api.validateFormula(formula);
      setFormulaError(res.ok ? null : res.error);
    } catch {
      setFormulaError(null); // validation is best-effort; run will surface real errors
    }
  }

  async function run() {
    setBusy("run");
    setError(null);
    try {
      onCycleChange(await api.runPartB({ universe_ids: universeIds, formula, filters }));
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

      {/* Scoring console */}
      <div className="scan-console">
        <div className="scan-console-head">
          <span>Scoring console</span>
          <button type="button" className="link-btn" onClick={() => changeFormula(DEFAULT_FORMULA)}
                  disabled={inputsDisabled}>Reset to default</button>
        </div>
        <textarea
          className="scan-formula-input"
          rows={3}
          value={formula}
          disabled={inputsDisabled}
          onChange={(e) => changeFormula(e.target.value)}
          onBlur={checkFormula}
          spellCheck={false}
        />
        <div className="scan-help">{TERM_HELP}</div>
        {formulaError && <p className="error-box">{formulaError}</p>}
      </div>

      {/* Run — directly under the console */}
      <div className="panel-actions">
        <button onClick={run}
                disabled={busy !== null || advanced || universeIds.length === 0 || !!formulaError}>
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
