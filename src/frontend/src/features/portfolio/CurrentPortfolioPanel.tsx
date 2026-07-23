import { useEffect, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { Holding, HoldingStatus, PortfolioSummary } from "../../api/types";

const STATUSES: HoldingStatus[] = ["ACTIVE", "PENDING_EXIT", "EXITED"];

const inr = (n: number | null | undefined) =>
  n == null ? "—" : `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
const signed = (n: number | null | undefined) =>
  n == null ? "—" : `${n < 0 ? "−" : "+"}${inr(Math.abs(n)).slice(1)}`;
const STOP_PCT = 0.8; // 20% stop below entry (Rulebook)

function blankHolding(): Holding {
  return {
    symbol: "", qty: null, entry_price: null, entry_date: "", stop_price: null,
    sleeve: "", sector: "", status: "ACTIVE", exit_reason: "", marked_on: "",
  };
}

// Step-0 — Current Portfolio: a direct, editable view of the holdings ledger.
// Persistent across cycles (positions change when trades fill), so it saves
// immediately rather than being a gated run/advance step.
export function CurrentPortfolioPanel({ onSaved }: { onSaved?: () => void }) {
  const [rows, setRows] = useState<Holding[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [summary, setSummary] = useState<PortfolioSummary | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  // Valuation is derived from the SAVED ledger, so it refreshes after a save —
  // never from the unsaved edits on screen, which would report a fiction.
  function loadSummary(refresh = false) {
    if (refresh) setRefreshing(true);
    api.getPortfolioSummary(refresh)
      .then(setSummary)
      .catch(() => setSummary(null))
      .finally(() => setRefreshing(false));
  }

  useEffect(() => {
    api.getHoldings()
      .then((h) => setRows(h))
      .catch((e) => setError(e instanceof ApiRequestError ? e.message : String(e)))
      .finally(() => setLoading(false));
    loadSummary();
  }, []);

  function patch(i: number, p: Partial<Holding>) {
    setRows((rs) => rs.map((r, idx) => {
      if (idx !== i) return r;
      const next = { ...r, ...p };
      // An ACTIVE position carries no exit metadata — drop any stale reason/date.
      if (next.status === "ACTIVE") {
        next.exit_reason = "";
        next.marked_on = "";
      }
      return next;
    }));
    setDirty(true);
    setNote(null);
  }
  const numOrNull = (v: string) => (v === "" ? null : Number(v));

  function addRow() {
    setRows((rs) => [...rs, blankHolding()]);
    setDirty(true);
  }
  function removeRow(i: number) {
    setRows((rs) => rs.filter((_, idx) => idx !== i));
    setDirty(true);
  }

  // On leaving the symbol cell, auto-fill sector/sleeve (and stop from entry).
  async function autofill(i: number) {
    const sym = rows[i]?.symbol?.trim();
    if (!sym) return;
    try {
      const res = await api.lookupSymbol(sym);
      patch(i, {
        symbol: res.symbol,
        sector: rows[i].sector || res.sector,
        sleeve: rows[i].sleeve || res.sleeve,
      });
    } catch {
      /* auto-fill is best-effort */
    }
  }

  function applyStop(i: number) {
    const entry = rows[i].entry_price;
    if (entry != null && rows[i].stop_price == null) {
      patch(i, { stop_price: Math.round(entry * STOP_PCT * 100) / 100 });
    }
  }

  async function save() {
    setSaving(true);
    setError(null);
    setNote(null);
    try {
      const cleaned = rows.filter((r) => r.symbol.trim() !== "")
                          .map((r) => ({ ...r, symbol: r.symbol.trim().toUpperCase() }));
      const saved = await api.saveHoldings(cleaned);
      setRows(saved);
      setDirty(false);
      setNote(`Saved ${saved.length} position(s).`);
      loadSummary();
      onSaved?.();
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="muted">Loading portfolio…</p>;

  const activeCount = rows.filter((r) => r.status === "ACTIVE").length;
  const valueOf = (symbol: string) =>
    summary?.positions.find((p) => p.symbol === symbol.trim().toUpperCase());

  return (
    <>
      <p className="panel-sub muted">
        Your live positions. Edit freely — this is the ledger every later step reads
        (rank exits, RED-regime exits, risk caps, the Monday plan).
      </p>

      <div className="scan-summary">
        {rows.length} position(s) · {activeCount} ACTIVE
        {dirty && <span className="unsaved"> · unsaved changes</span>}
      </div>

      {summary && summary.held_count > 0 && (
        <>
          <div className="port-stats">
            <div className="stat">
              <span className="stat-label">Capital deployed</span>
              <span className="stat-value">{inr(summary.capital_deployed)}</span>
              <span className="stat-note">{summary.held_count} held at cost</span>
            </div>
            <div className="stat">
              <span className="stat-label">Market value</span>
              <span className="stat-value">{inr(summary.market_value)}</span>
              <span className="stat-note">at the {summary.price_date || summary.as_of} close</span>
            </div>
            <div className="stat">
              <span className="stat-label">Unrealised P&amp;L</span>
              <span className={`stat-value ${(summary.unrealised_pnl ?? 0) < 0 ? "v-strike" : "v-pass"}`}>
                {signed(summary.unrealised_pnl)}
              </span>
              <span className="stat-note">
                {summary.unrealised_pnl_pct == null ? "—"
                  : `${summary.unrealised_pnl_pct > 0 ? "+" : ""}${summary.unrealised_pnl_pct}%`}
              </span>
            </div>
            <div className="stat">
              <span className="stat-label">Open risk</span>
              <span className="stat-value">{inr(summary.open_risk)}</span>
              <span className="stat-note">if every stop fired</span>
            </div>
          </div>
          {dirty && (
            <p className="stale-hint">
              Valuation reflects the <b>saved</b> ledger — save to include your edits.
            </p>
          )}
          {summary.warnings.map((w, i) => <p key={i} className="stale-hint">{w}</p>)}
        </>
      )}

      <div className="table-toolbar">
        <span className="muted">
          {summary?.price_date
            ? `LTP at the ${summary.price_date} close`
            : "LTP unavailable"}
        </span>
        <button onClick={() => loadSummary(true)} disabled={refreshing}
                title="Re-pull live prices instead of serving the cache">
          {refreshing ? "Refreshing…" : "↻ Refresh LTP"}
        </button>
      </div>

      <div className="scan-table-wrap">
        <table className="scan-table port-table">
          <thead>
            <tr>
              <th>Symbol</th><th className="num">Qty</th><th className="num">Entry ₹</th>
              <th className="num">LTP</th><th className="num">P&amp;L ₹</th>
              <th className="num">Value</th>
              <th>Entry date</th><th className="num">Stop ₹</th>
              <th>Sector</th><th>Sleeve</th><th>Status</th><th>Exit info</th><th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={r.status !== "ACTIVE" ? "row-ineligible" : ""}>
                <td>
                  <input className="cell w-sym" value={r.symbol} placeholder="TICKER"
                         onChange={(e) => patch(i, { symbol: e.target.value.toUpperCase() })}
                         onBlur={() => autofill(i)} />
                </td>
                <td className="num">
                  <input className="cell w-num" type="number" value={r.qty ?? ""}
                         onChange={(e) => patch(i, { qty: numOrNull(e.target.value) })} />
                </td>
                <td className="num">
                  <input className="cell w-num" type="number" step="any" value={r.entry_price ?? ""}
                         onChange={(e) => patch(i, { entry_price: numOrNull(e.target.value) })}
                         onBlur={() => applyStop(i)} />
                </td>
                {(() => {
                  const v = valueOf(r.symbol);
                  const up = v?.last_close != null && v.entry_price != null
                    && v.last_close >= v.entry_price;
                  return (
                    <>
                      <td className="num">
                        {v?.last_close == null ? <span className="muted">—</span> : (
                          <span className={up ? "v-pass" : "v-strike"}
                                title={`close of ${v.price_date}`}>
                            {v.last_close.toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                          </span>
                        )}
                      </td>
                      <td className="num">
                        {v?.pnl == null ? <span className="muted">—</span> : (
                          <span className={v.pnl < 0 ? "v-strike" : "v-pass"}>
                            {signed(v.pnl)}
                            <span className="pnl-pct">
                              {" "}{v.pnl_pct! > 0 ? "+" : ""}{v.pnl_pct}%
                            </span>
                          </span>
                        )}
                      </td>
                      <td className="num">
                        {v?.market_value == null ? <span className="muted">—</span> : (
                          <span title={v.weight_pct ? `${v.weight_pct}% of portfolio` : undefined}>
                            {inr(v.market_value)}
                          </span>
                        )}
                      </td>
                    </>
                  );
                })()}
                <td>
                  <input className="cell w-date" type="date" value={r.entry_date || ""}
                         onChange={(e) => patch(i, { entry_date: e.target.value })} />
                </td>
                <td className="num">
                  <input className="cell w-num w-stop" type="number" step="any" value={r.stop_price ?? ""}
                         onChange={(e) => patch(i, { stop_price: numOrNull(e.target.value) })} />
                </td>
                <td>
                  <input className="cell w-txt" value={r.sector}
                         onChange={(e) => patch(i, { sector: e.target.value })} />
                </td>
                <td>
                  <input className="cell w-txt" value={r.sleeve}
                         onChange={(e) => patch(i, { sleeve: e.target.value })} />
                </td>
                <td>
                  <select className="cell w-status" value={r.status}
                          onChange={(e) => patch(i, { status: e.target.value as HoldingStatus })}>
                    {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </td>
                <td className="flags">
                  {r.exit_reason
                    ? `${r.exit_reason}${r.marked_on ? ` · ${r.marked_on}` : ""}`
                    : ""}
                </td>
                <td>
                  <button className="ghost row-del" title="Remove position"
                          onClick={() => removeRow(i)}>✕</button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={13} className="muted">No positions yet — add your first one.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {error && <p className="error-box">{error}</p>}
      {note && <p className="save-note">{note}</p>}

      <div className="panel-actions">
        <button onClick={addRow} disabled={saving}>+ Add position</button>
        <button className="primary" onClick={save} disabled={saving || !dirty}>
          {saving ? "Saving…" : "Save portfolio"}
        </button>
      </div>
    </>
  );
}
