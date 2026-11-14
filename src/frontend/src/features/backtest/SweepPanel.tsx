import { useEffect, useRef, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type {
  RegimeOptions, RunStatus, SweepRecord, SweepRunRequest, UniverseSummary,
} from "../../api/types";
import { SweepResult } from "./SweepResult";

const LIVE: RunStatus[] = ["queued", "fetching", "running"];
const isLive = (s: RunStatus) => LIVE.includes(s);

const DEFAULTS: Omit<SweepRunRequest, "universe_id"> = {
  label: "", index_id: "nifty500", filter_id: "st_1_2_5",
  train_end: "2016-12-31", test_start: "2017-01-01",
  weight_step: 20, noise_samples: 120, cost_bps: 30, slippage_bps: 25,
};

// Walk-forward formula sweep. Deliberately a distinct sub-section: it is a
// research search, not a single backtest, and its output is a VERDICT about
// whether an improvement is real — not just a number to copy.
export function SweepPanel({ universes, options }:
                           { universes: UniverseSummary[]; options: RegimeOptions | null }) {
  const [sweeps, setSweeps] = useState<SweepRecord[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [form, setForm] = useState<SweepRunRequest>({ universe_id: "", ...DEFAULTS });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  function refresh() {
    api.listSweeps().then(setSweeps).catch(() => undefined);
  }
  useEffect(() => {
    refresh();
    setForm((f) => (f.universe_id || !universes.length
      ? f : { ...f, universe_id: universes[0].universe_id }));
  }, [universes]);

  useEffect(() => {
    const live = sweeps.some((s) => isLive(s.status));
    if (!live) {
      if (timer.current) window.clearInterval(timer.current);
      timer.current = null;
      return;
    }
    if (timer.current) return;
    timer.current = window.setInterval(refresh, 2000);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
      timer.current = null;
    };
  }, [sweeps]);

  function set<K extends keyof SweepRunRequest>(k: K, v: SweepRunRequest[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function start() {
    setBusy(true);
    setError(null);
    try {
      await api.startSweep(form);
      refresh();
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      refresh();
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // Preview the comparison count — a bigger grid is a higher bar the winner
  // must clear, so it belongs in front of the user before they run.
  const gridSize = (() => {
    const step = form.weight_step ?? 20;
    let g = 0;
    for (let w6 = 0; w6 <= 100; w6 += step)
      for (let w3 = 0; w3 <= 100 - w6; w3 += step) g += 4;
    return g;
  })();

  return (
    <>
      <h4 className="jr-head">Formula sweep <span className="muted">(walk-forward)</span></h4>
      <p className="verdict-def">
        Searches weightings of the momentum score, tuning on a training period and judging
        on a later one it never saw. It always measures a <b>noise floor</b> first — how much
        a random formula spread produces — so a winner is read against luck, not in isolation.
        These are fast SCREEN numbers; confirm any candidate with a full run.
      </p>

      {error && <p className="error-box">{error}</p>}

      <div className="scan-filters">
        <label>
          <span>Universe</span>
          <select value={form.universe_id} onChange={(e) => set("universe_id", e.target.value)}>
            {!universes.length && <option value="">build one first</option>}
            {universes.map((u) => (
              <option key={u.universe_id} value={u.universe_id}>{u.universe_id} ({u.size})</option>
            ))}
          </select>
        </label>
        <label>
          <span>Label</span>
          <input value={form.label ?? ""} placeholder="what are you searching?"
                 onChange={(e) => set("label", e.target.value)} />
        </label>
        <label>
          <span>Regime index</span>
          <select value={form.index_id} onChange={(e) => set("index_id", e.target.value)}>
            {options?.indices.map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}
          </select>
        </label>
        <label>
          <span>Regime filter</span>
          <select value={form.filter_id} onChange={(e) => set("filter_id", e.target.value)}>
            {options?.filters.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
          </select>
        </label>
      </div>

      <div className="scan-filters">
        <label>
          <span>Train through</span>
          <input type="date" value={form.train_end}
                 onChange={(e) => set("train_end", e.target.value)} />
        </label>
        <label>
          <span>Test from</span>
          <input type="date" value={form.test_start}
                 onChange={(e) => set("test_start", e.target.value)} />
          <span className="field-hint">must be after the training period</span>
        </label>
        <label>
          <span>Weight step (%)</span>
          <input type="number" min={5} max={50} step={5} value={form.weight_step}
                 onChange={(e) => set("weight_step", Number(e.target.value))} />
          <span className="field-hint">~{gridSize} formulas · finer = slower</span>
        </label>
        <label>
          <span>Noise samples</span>
          <input type="number" min={0} max={500} step={20} value={form.noise_samples}
                 onChange={(e) => set("noise_samples", Number(e.target.value))} />
          <span className="field-hint">random formulas for the floor</span>
        </label>
      </div>

      <div className="panel-actions">
        <button className="primary" disabled={!form.universe_id || busy} onClick={start}>
          {busy ? "Starting…" : "Run sweep"}
        </button>
        <span className="muted">
          Builds the feature panel once (~min), then screens {gridSize} formulas in seconds.
        </span>
      </div>

      {sweeps.length > 0 && (
        <div className="scan-table-wrap" style={{ marginTop: 12 }}>
          <table className="scan-table">
            <thead>
              <tr>
                <th>Sweep</th><th>Status</th><th className="num">Winner OOS</th>
                <th className="num">%ile</th><th className="num">Beat base</th><th></th>
              </tr>
            </thead>
            <tbody>
              {sweeps.map((s) => {
                const v = s.verdict;
                return (
                  <tr key={s.sweep_id} className={openId === s.sweep_id ? "row-selected" : ""}>
                    <td>
                      <b>{s.label || s.sweep_id}</b>
                      <div className="muted run-sub">{String(s.request.universe_id)}</div>
                    </td>
                    <td>
                      <span className={`pill bt-${s.status}`}>{s.status}</span>
                      {isLive(s.status) && (
                        <div className="bt-progress">
                          <div className="bt-progress-bar" style={{ width: `${s.progress.pct}%` }} />
                          <span className="muted">{s.progress.detail}</span>
                        </div>
                      )}
                      {s.error && <div className="flags">{s.error}</div>}
                    </td>
                    <td className="num">{v ? `${v.winner_test_cagr}%` : "—"}</td>
                    <td className="num">{v ? `${v.winner_test_percentile}th` : "—"}</td>
                    <td className="num">{v ? `${v.pct_beating_baseline_test}%` : "—"}</td>
                    <td className="bt-actions">
                      {s.status === "done" && (
                        <button className="ghost"
                                onClick={() => setOpenId(openId === s.sweep_id ? null : s.sweep_id)}>
                          {openId === s.sweep_id ? "Hide" : "View"}
                        </button>
                      )}
                      {isLive(s.status) && (
                        <button className="ghost" disabled={busy}
                                onClick={() => act(() => api.cancelSweep(s.sweep_id))}>Cancel</button>
                      )}
                      <button className="ghost danger" disabled={busy}
                              onClick={() => act(async () => {
                                await api.deleteSweep(s.sweep_id);
                                if (openId === s.sweep_id) setOpenId(null);
                              })}>✕</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {openId && <SweepResult sweepId={openId} />}
    </>
  );
}
