import { useEffect, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { Cycle, RegimeOptions } from "../../api/types";
import { ReopenStepButton } from "../../components/ReopenStepButton";

interface Props {
  cycle: Cycle;
  onCycleChange: (cycle: Cycle) => void;
}

// Part A body — the regime gate. Replaces opening TradingView: pick an index +
// filter, Run, read the verdict, then advance the gate.
export function RegimeGatePanel({ cycle, onCycleChange }: Props) {
  const step = cycle.steps.part_a_regime_gate;
  const out = step.output;
  const [busy, setBusy] = useState<null | "run" | "advance">(null);
  const [error, setError] = useState<string | null>(null);
  const [options, setOptions] = useState<RegimeOptions | null>(null);
  const [indexId, setIndexId] = useState<string>("");
  const [filterId, setFilterId] = useState<string>("");

  const gateDecided = step.status === "done" && out !== null;
  const alreadyAdvanced = cycle.current_step !== "part_a_regime_gate";
  const selectedIndex = options?.indices.find((o) => o.id === indexId);

  // The selection that produced the current result (from the output, or the
  // step input on a failed run). If the dropdowns no longer match it, the shown
  // result is stale and gets hidden until the user re-runs.
  const ranSelection = out
    ? { index_id: out.index_id, filter_id: out.filter_id }
    : (step.input as { index_id?: string; filter_id?: string } | null);
  const dirty =
    !!ranSelection &&
    (indexId !== ranSelection.index_id || filterId !== ranSelection.filter_id);
  const showResult = (step.status === "done" || step.status === "failed") && !dirty;

  // Load the dropdown options once; seed the selection from a prior run or the
  // server defaults.
  useEffect(() => {
    let alive = true;
    api.getRegimeOptions().then((opts) => {
      if (!alive) return;
      setOptions(opts);
      const savedIndex = (step.input?.index_id as string) ?? out?.index_id;
      const savedFilter = (step.input?.filter_id as string) ?? out?.filter_id;
      setIndexId(savedIndex ?? opts.default_index_id);
      setFilterId(savedFilter ?? opts.default_filter_id);
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function act(kind: "run" | "advance") {
    setBusy(kind);
    setError(null);
    try {
      onCycleChange(
        kind === "run" ? await api.runPartA(indexId, filterId) : await api.advancePartA()
      );
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="regime-config">
        <label>
          <span>Index</span>
          <select
            value={indexId}
            disabled={!options || busy !== null || alreadyAdvanced}
            onChange={(e) => setIndexId(e.target.value)}
          >
            {options?.indices.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
                {o.kind === "etf_proxy" ? "  · ETF" : ""}
              </option>
            ))}
          </select>
          {selectedIndex && (
            <small className="field-hint">
              {selectedIndex.kind === "etf_proxy"
                ? `Tracked via ${selectedIndex.note} (${selectedIndex.ticker})`
                : `Live index (${selectedIndex.ticker})`}
            </small>
          )}
        </label>
        <label>
          <span>Regime filter</span>
          <select
            value={filterId}
            disabled={!options || busy !== null || alreadyAdvanced}
            onChange={(e) => setFilterId(e.target.value)}
          >
            {options?.filters.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {showResult && step.status === "failed" && (
        <p className="error-box">Failed: {step.error}</p>
      )}

      {dirty && (
        <p className="stale-hint">Selection changed — run to update the regime.</p>
      )}

      {showResult && gateDecided && out && (
        <div className={`verdict verdict-${out.regime.toLowerCase()}`}>
          <div className="verdict-flag">{out.regime}</div>
          <p className="verdict-def">
            {out.filter_label} on {out.index_label}
          </p>
          <dl className="metrics">
            <div><dt>Week end</dt><dd>{out.week_end}</dd></div>
            <div><dt>Close</dt><dd>{out.index_close}</dd></div>
            <div><dt>{out.line_label || "Reference"}</dt><dd>{out.line_value ?? "—"}</dd></div>
            <div><dt>Source</dt><dd className="src">{out.data_source}</dd></div>
          </dl>
          {out.reentry_required && (
            <p className="reentry">RED → GREEN shift: mandatory re-entry Monday open.</p>
          )}
          <ul className="actions">
            {out.actions.map((a, i) => <li key={i}>{a}</li>)}
          </ul>
        </div>
      )}

      {error && <p className="error-box">{error}</p>}

      <div className="panel-actions">
        <button onClick={() => act("run")} disabled={busy !== null || alreadyAdvanced || !options}>
          {busy === "run" ? "Running…" : gateDecided && !dirty ? "Re-run" : "Run regime check"}
        </button>

        {showResult && gateDecided && !alreadyAdvanced && out && (
          <button className="primary" onClick={() => act("advance")} disabled={busy !== null}>
            {out.regime === "GREEN" ? "Acknowledge & proceed to Jerald Score" : "Acknowledge RED & mark exits"}
          </button>
        )}

        {alreadyAdvanced && (
          <>
            <span className="advanced-note">
              {cycle.outcome === "red_terminated"
                ? "RED — ritual terminated. Cancel your GTTs manually; sell marked names Monday open."
                : "Advanced — see the next step below."}
            </span>
            <ReopenStepButton stepId="part_a_regime_gate" onCycleChange={onCycleChange} />
          </>
        )}
      </div>
    </>
  );
}
