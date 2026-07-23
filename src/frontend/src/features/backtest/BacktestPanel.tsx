import { useEffect, useRef, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type {
  AltAssetOption,
  BacktestRunRequest,
  IndexCoverage,
  RegimeOptions,
  RunRecord,
  RunStatus,
  UniverseOption,
  UniverseSummary,
} from "../../api/types";
import { MultiSelect } from "../../components/MultiSelect";
import { RunResult } from "./RunResult";

const LIVE: RunStatus[] = ["queued", "fetching", "running"];
const isLive = (s: RunStatus) => LIVE.includes(s);

const DEFAULTS: Omit<BacktestRunRequest, "universe_id"> = {
  label: "",
  starting_capital: 1_000_000,
  position_weight_pct: 10,
  stop_multiplier: 0.8,
  cost_bps: 30,
  slippage_bps: 25,
  fetch_prices: true,
};

// Backtest — a research tool, deliberately its own flow. It shares the strategy
// core with the Friday ritual but nothing else: its own data tree, its own
// prices, and no access to the ledger, cycles or journal.
export function BacktestPanel() {
  const [universes, setUniverses] = useState<UniverseSummary[]>([]);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [openRun, setOpenRun] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  // Run form
  const [form, setForm] = useState<BacktestRunRequest>({ universe_id: "", ...DEFAULTS });
  const [options, setOptions] = useState<RegimeOptions | null>(null);
  const [altAssets, setAltAssets] = useState<AltAssetOption[]>([]);
  const [coverage, setCoverage] = useState<IndexCoverage[]>([]);

  // Universe builder
  const [indexOptions, setIndexOptions] = useState<UniverseOption[]>([]);
  const [newId, setNewId] = useState("midsmall400");
  const [sourceIds, setSourceIds] = useState<string[]>(["midsmall400"]);

  const timer = useRef<number | null>(null);

  async function refresh() {
    try {
      const [u, r] = await Promise.all([api.listBacktestUniverses(), api.listBacktestRuns()]);
      setUniverses(u);
      setRuns(r);
      setForm((f) => (f.universe_id || u.length === 0
        ? f : { ...f, universe_id: u[0].universe_id }));
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    }
  }

  useEffect(() => {
    refresh();
    api.getRegimeOptions().then(setOptions).catch(() => setOptions(null));
    api.listAltAssets().then(setAltAssets).catch(() => setAltAssets([]));
    api.getIndexCoverage().then(setCoverage).catch(() => setCoverage([]));
    api.getUniverseOptions()
      .then((o) => setIndexOptions(o.filter((x) => x.id !== "holdings")))
      .catch(() => setIndexOptions([]));
  }, []);

  // Poll only while something is actually in flight — a finished list is static,
  // so an idle Backtest tab makes no requests at all.
  useEffect(() => {
    const live = runs.some((r) => isLive(r.status));
    if (!live) {
      if (timer.current) window.clearInterval(timer.current);
      timer.current = null;
      return;
    }
    if (timer.current) return;
    timer.current = window.setInterval(() => {
      api.listBacktestRuns().then(setRuns).catch(() => undefined);
    }, 1500);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
      timer.current = null;
    };
  }, [runs]);

  function set<K extends keyof BacktestRunRequest>(k: K, v: BacktestRunRequest[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    setError(null);
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const selected = universes.find((u) => u.universe_id === form.universe_id);
  const missingPrices = selected ? selected.size - selected.cached_prices : 0;

  // The regime index — not the universe — sets how far back a run can go.
  const indexCov = coverage.find(
    (c) => c.id === (form.index_id ?? options?.default_index_id));
  const tooEarly = !!(form.start && indexCov?.first_tradable
    && form.start < indexCov.first_tradable);

  return (
    <>
      <p className="panel-sub muted">
        Replays the strategy over history using the <b>same rules the Friday ritual runs</b> —
        the regime gate, Jerald Score, rank exits, technical vetoes and risk caps all come from
        one shared core. Runs happen in the background on separate data; nothing here can touch
        your ledger, cycles or journal.
      </p>

      {error && <p className="error-box">{error}</p>}

      {/* --- 1. universes --------------------------------------------------- */}
      <h4 className="jr-head">Universes</h4>
      <p className="verdict-def">
        A universe is a fixed list of tradable names, snapshotted from an index today.
        Because membership is fixed, results carry <b>survivorship bias</b> — names that
        were delisted or demoted are absent, which flatters returns.
      </p>

      <div className="scan-filters">
        <label>
          <span>New universe name</span>
          <input value={newId} onChange={(e) => setNewId(e.target.value.trim())}
                 placeholder="e.g. midsmall400" />
        </label>
        <label>
          <span>Build from indices</span>
          <MultiSelect
            options={indexOptions.map((o) => ({ id: o.id, label: o.label }))}
            selected={sourceIds}
            onChange={setSourceIds}
            placeholder="pick one or more"
          />
        </label>
        <div className="filter-actions">
          <button onClick={() => act("build", () => api.buildBacktestUniverse(newId, sourceIds))}
                  disabled={!newId || sourceIds.length === 0 || busy !== null}>
            {busy === "build" ? "Building…" : "Build universe"}
          </button>
        </div>
      </div>

      {universes.length > 0 && (
        <div className="scan-table-wrap">
          <table className="scan-table">
            <thead>
              <tr>
                <th>Universe</th><th className="num">Names</th><th className="num">Microcap</th>
                <th className="num">Prices cached</th><th>History</th><th></th>
              </tr>
            </thead>
            <tbody>
              {universes.map((u) => (
                <tr key={u.universe_id}>
                  <td><b>{u.universe_id}</b></td>
                  <td className="num">{u.size}</td>
                  <td className="num">{u.microcap_count}</td>
                  <td className="num">
                    <span className={u.cached_prices === u.size ? "v-pass" : ""}>
                      {u.cached_prices}
                    </span>
                    {u.cached_prices < u.size && (
                      <span className="muted"> / {u.size}</span>)}
                  </td>
                  <td className="muted">
                    {u.first_session
                      ? `${u.first_session} → ${u.last_session}`
                      : "not fetched yet — the first run downloads it"}
                  </td>
                  <td>
                    <button className="ghost danger" disabled={busy !== null}
                            onClick={() => act("del", () =>
                              api.deleteBacktestUniverse(u.universe_id))}>✕</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* --- 2. new run ----------------------------------------------------- */}
      <h4 className="jr-head">New run</h4>

      <div className="scan-filters">
        <label>
          <span>Universe</span>
          <select value={form.universe_id} onChange={(e) => set("universe_id", e.target.value)}>
            {universes.length === 0 && <option value="">build one first</option>}
            {universes.map((u) => (
              <option key={u.universe_id} value={u.universe_id}>
                {u.universe_id} ({u.size})
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Label</span>
          <input value={form.label ?? ""} placeholder="what are you testing?"
                 onChange={(e) => set("label", e.target.value)} />
        </label>
        <label>
          <span>Index (regime)</span>
          <select value={form.index_id ?? options?.default_index_id ?? ""}
                  onChange={(e) => set("index_id", e.target.value)}>
            {options?.indices.map((i) => {
              const c = coverage.find((x) => x.id === i.id);
              return (
                <option key={i.id} value={i.id}>
                  {i.label}{c?.first_tradable ? ` — from ${c.first_tradable.slice(0, 7)}` : ""}
                </option>
              );
            })}
          </select>
          <span className="field-hint">
            {indexCov?.first_tradable
              ? `history ${indexCov.first_session} → usable from ${indexCov.first_tradable}`
              : "no cached history yet — the first run downloads it"}
          </span>
        </label>
        <label>
          <span>Regime filter</span>
          <select value={form.filter_id ?? options?.default_filter_id ?? ""}
                  onChange={(e) => set("filter_id", e.target.value)}>
            {options?.filters.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
          </select>
        </label>
      </div>

      <div className="scan-filters">
        <label>
          <span>Start</span>
          <input type="date" value={form.start ?? ""}
                 onChange={(e) => set("start", e.target.value || null)} />
        </label>
        <label>
          <span>End</span>
          <input type="date" value={form.end ?? ""}
                 onChange={(e) => set("end", e.target.value || null)} />
        </label>
        <label>
          <span>Starting capital ₹</span>
          <input type="number" value={form.starting_capital}
                 onChange={(e) => set("starting_capital", Number(e.target.value))} />
        </label>
        <label>
          <span>Weight per name %</span>
          <input type="number" step="0.5" value={form.position_weight_pct}
                 onChange={(e) => set("position_weight_pct", Number(e.target.value))} />
        </label>
      </div>

      <div className="scan-filters">
        <label>
          <span>Stop multiplier</span>
          <input type="number" step="0.01" value={form.stop_multiplier}
                 onChange={(e) => set("stop_multiplier", Number(e.target.value))} />
          <span className="field-hint">0.80 = the Rulebook's 20% stop</span>
        </label>
        <label>
          <span>Costs (bps)</span>
          <input type="number" value={form.cost_bps}
                 onChange={(e) => set("cost_bps", Number(e.target.value))} />
          <span className="field-hint">STT, exchange, stamp duty, GST</span>
        </label>
        <label>
          <span>Slippage (bps)</span>
          <input type="number" value={form.slippage_bps}
                 onChange={(e) => set("slippage_bps", Number(e.target.value))} />
          <span className="field-hint">adverse fill vs the reference price</span>
        </label>
        <label className="check-label">
          <input type="checkbox" checked={form.fetch_prices ?? true}
                 onChange={(e) => set("fetch_prices", e.target.checked)} />
          <span>Fetch missing prices from yfinance first</span>
        </label>
      </div>

      {/* RED weeks sit in cash under the Rulebook. This tests parking that
          capital instead — a research question, not a change to the ritual. */}
      <div className="alt-asset">
        <label className="toggle-row">
          <input type="checkbox" className="switch"
                 checked={!!form.alt_asset_id}
                 onChange={(e) => {
                   set("alt_asset_id", e.target.checked ? (altAssets[0]?.id ?? null) : null);
                   if (e.target.checked && !form.alt_allocation_pct) set("alt_allocation_pct", 100);
                 }} />
          <span className="toggle-label">Invest RED-week cash in an uncorrelated asset</span>
        </label>

        {form.alt_asset_id && (
          <div className="scan-filters alt-fields">
            <label>
              <span>Asset</span>
              <select value={form.alt_asset_id}
                      onChange={(e) => set("alt_asset_id", e.target.value)}>
                {altAssets.map((a) => (
                  <option key={a.id} value={a.id}>{a.label}</option>
                ))}
              </select>
              <span className="field-hint">
                {altAssets.find((a) => a.id === form.alt_asset_id)?.note}
              </span>
            </label>
            <label>
              <span>Allocation (%)</span>
              <input type="number" min={1} max={100}
                     value={form.alt_allocation_pct ?? 100}
                     onChange={(e) => set("alt_allocation_pct",
                       Math.min(100, Math.max(1, Number(e.target.value))))} />
              <span className="field-hint">1–100% of equity; the rest stays in cash</span>
            </label>
          </div>
        )}

        {form.alt_asset_id && (
          <p className="stale-hint">
            History for this asset starts{" "}
            {altAssets.find((a) => a.id === form.alt_asset_id)?.first_session || "later"} —
            RED weeks before that stay in cash, so the effect applies to part of the run only.
            Compare against <b>Liquid fund</b> rather than cash: idle money would realistically
            earn ~6–7%.
          </p>
        )}
      </div>

      <div className="scan-filters">
        <label style={{ flex: 3 }}>
          <span>Scoring formula</span>
          <input value={form.formula ?? ""} placeholder="leave blank for the Jerald Score"
                 onChange={(e) => set("formula", e.target.value || undefined)} />
        </label>
      </div>

      {tooEarly && (
        <p className="warn-box">
          <b>Your start date will be moved.</b> {indexCov?.label} has data only from{" "}
          {indexCov?.first_session}, and the score needs ~300 days of warm-up, so this run
          can only begin at <b>{indexCov?.first_tradable}</b> — not {form.start}.
          Pick an index with longer history (NIFTY 500 reaches back to 2005) to test
          earlier periods. The regime index gates the run; it is not the universe.
        </p>
      )}

      {missingPrices > 0 && (
        <p className="stale-hint">
          {missingPrices} of {selected?.size} names have no cached history. The first run will
          download them from yfinance — that can take several minutes.
        </p>
      )}

      <div className="panel-actions">
        <button className="primary" disabled={!form.universe_id || busy !== null}
                onClick={() => act("start", () => api.startBacktest(form))}>
          {busy === "start" ? "Starting…" : "Run backtest"}
        </button>
        <span className="muted">Runs in the background — you can leave this tab.</span>
      </div>

      {/* --- 3. runs -------------------------------------------------------- */}
      <h4 className="jr-head">Runs <span className="muted">({runs.length})</span></h4>

      {runs.length === 0 ? (
        <p className="stub-note">No runs yet. Build a universe, then start one above.</p>
      ) : (
        <div className="scan-table-wrap">
          <table className="scan-table">
            <thead>
              <tr>
                <th>Run</th><th>Status</th><th className="num">CAGR</th>
                <th className="num">Sharpe</th><th className="num">Max DD</th>
                <th className="num">vs Index</th><th></th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const m = r.summary?.metrics;
                const b = r.summary?.benchmark;
                const edge = m && b ? m.cagr_pct - b.cagr_pct : null;
                return (
                  <tr key={r.run_id} className={openRun === r.run_id ? "row-selected" : ""}>
                    <td>
                      <b>{r.label || r.run_id}</b>
                      <div className="muted run-sub">{r.run_id}</div>
                    </td>
                    <td>
                      <span className={`pill bt-${r.status}`}>{r.status}</span>
                      {isLive(r.status) && (
                        <div className="bt-progress">
                          <div className="bt-progress-bar"
                               style={{ width: `${r.progress.pct}%` }} />
                          <span className="muted">{r.progress.detail}</span>
                        </div>
                      )}
                      {r.error && <div className="flags">{r.error}</div>}
                    </td>
                    <td className="num">{m ? `${m.cagr_pct}%` : "—"}</td>
                    <td className="num">{m ? m.sharpe : "—"}</td>
                    <td className="num">{m ? `${m.max_drawdown_pct}%` : "—"}</td>
                    <td className="num">
                      {edge == null ? "—" : (
                        <span className={edge >= 0 ? "v-pass" : "v-strike"}>
                          {edge > 0 ? "+" : ""}{edge.toFixed(2)}%
                        </span>
                      )}
                    </td>
                    <td className="bt-actions">
                      {r.status === "done" && (
                        <button className="ghost"
                                onClick={() => setOpenRun(openRun === r.run_id ? null : r.run_id)}>
                          {openRun === r.run_id ? "Hide" : "View"}
                        </button>
                      )}
                      {isLive(r.status) && (
                        <button className="ghost" disabled={busy !== null}
                                onClick={() => act("cancel", () => api.cancelBacktest(r.run_id))}>
                          Cancel
                        </button>
                      )}
                      <button className="ghost danger" disabled={busy !== null}
                              onClick={() => act("delrun", async () => {
                                await api.deleteBacktestRun(r.run_id);
                                if (openRun === r.run_id) setOpenRun(null);
                              })}>✕</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {openRun && <RunResult runId={openRun} />}
    </>
  );
}
