import { useState } from "react";
import { api, ApiRequestError } from "../api/client";
import type { Cycle } from "../api/types";

interface Props {
  stepId: string;
  onCycleChange: (cycle: Cycle) => void;
}

// Go back to an already-advanced step to edit + re-run it. Downstream steps are
// reset (they were derived from the result being changed).
export function ReopenStepButton({ stepId, onCycleChange }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function reopen() {
    if (!window.confirm(
      "Reopen this step for editing?\n\nLater steps will be reset and must be re-run. " +
      "Ledger changes already committed (e.g. holdings marked for exit) are not undone."
    )) return;
    setBusy(true);
    setError(null);
    try {
      onCycleChange(await api.reopenStep(stepId));
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button className="ghost" onClick={reopen} disabled={busy}>
        {busy ? "Reopening…" : "Reopen & edit"}
      </button>
      {error && <p className="error-box">{error}</p>}
    </>
  );
}
