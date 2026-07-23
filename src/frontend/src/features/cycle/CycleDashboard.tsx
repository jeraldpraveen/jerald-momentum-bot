import { useEffect, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { Cycle, CycleSteps, StepEnvelope } from "../../api/types";
import { AccordionStep } from "../../components/AccordionStep";
import { Tabs } from "../../components/Tabs";
import { PortfolioAuditPanel } from "../exitFlags/PortfolioAuditPanel";
import { FundamentalPanel } from "../fundamental/FundamentalPanel";
import { BacktestPanel } from "../backtest/BacktestPanel";
import { HistoryPanel } from "../history/HistoryPanel";
import { CurrentPortfolioPanel } from "../portfolio/CurrentPortfolioPanel";
import { RegimeGatePanel } from "../regimeGate/RegimeGatePanel";
import { JournalPanel } from "../journal/JournalPanel";
import { MondayPlanPanel } from "../phase3/MondayPlanPanel";
import { RiskCapsPanel } from "../riskCaps/RiskCapsPanel";
import { ScannerPanel } from "../scanner/ScannerPanel";
import { TechnicalPanel } from "../technical/TechnicalPanel";

interface Props {
  cycle: Cycle | null;
  noActiveCycle: boolean;
  onCycleChange: (cycle: Cycle) => void;
}

type StepKey = keyof CycleSteps;
type TabId = "portfolio" | "ritual" | "history" | "backtest";

const STEPS: { id: StepKey; title: string; blurb: string }[] = [
  {
    id: "part_a_regime_gate",
    title: "Regime Gate",
    blurb: "Weekly Supertrend on NIFTY MIDSMALLCAP 400 — GREEN proceeds, RED terminates.",
  },
  {
    id: "part_b_scanner",
    title: "Jerald Score",
    blurb: "Compute the Jerald momentum score across the universe, rank candidates, and drop names below ₹5 Cr median daily traded value. No Excel upload.",
  },
  {
    id: "part_c_exit_flags",
    title: "Portfolio Audit",
    blurb: "Flag holdings ranked > 50, or dropped from the index, for Monday exit.",
  },
  {
    id: "part_d_fundamental",
    title: "Fundamental Analysis",
    blurb: "Ranks 1–15 only. Eliminate if promoter pledging > 20%, interest coverage < 2 (non-financials), a pending structural action, or listed < 12 months.",
  },
  {
    id: "part_d_technical",
    title: "Technical Analysis",
    blurb: "Eliminate if price < 50-DMA, > 30% above 20-DMA, or RSI(14) > 82.",
  },
  {
    id: "part_d_risk_caps",
    title: "Risk Caps",
    blurb: "Cap at 3 names per sector and 4 from the Microcap 250 sleeve; fill 8–12 slots. Unfilled slots stay in cash until next Friday.",
  },
  {
    id: "part_e_journal",
    title: "Journal Finalize",
    blurb: "Record the verified regime, final asset list, and any flags to the journal.",
  },
  {
    id: "phase3_plan",
    title: "Monday Execution Plan",
    blurb: "Liquidations first — per ticker, cancel that GTT then sell (never bulk-cancel). Then buys at ~10% weight, with stops at fill × 0.80.",
  },
];

export function CycleDashboard({ cycle, noActiveCycle, onCycleChange }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string>(cycle?.current_step ?? "part_a_regime_gate");
  // Always opens on Portfolio — a reload is a fresh look at the positions.
  const [tab, setTab] = useState<TabId>("portfolio");

  // Auto-expand the active step whenever the gate advances.
  useEffect(() => {
    if (cycle) setOpenId(cycle.current_step);
  }, [cycle?.current_step]);

  async function startCycle() {
    setBusy(true);
    setError(null);
    try {
      onCycleChange(await api.createCycle());
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // Finish/abandon the cycle but KEEP it as read-only history, then start fresh.
  async function closeAndStartNew() {
    if (cycle && cycle.outcome == null &&
        !window.confirm("Close the current cycle and start a new one? It is kept as history.")) {
      return;
    }
    await finishAndCreate(() => api.closeCycle());
  }

  // Permanently DELETE the cycle file (for throwaway/test cycles), then start fresh.
  async function deleteAndStartNew() {
    if (!window.confirm(
      `Permanently DELETE cycle ${cycle?.cycle_id}?\n\nThe file is removed — no history is kept. ` +
      "Ledger changes already committed are not undone.\n\nThis cannot be undone."
    )) return;
    await finishAndCreate(() => api.deleteCycle());
  }

  async function finishAndCreate(finish: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      try {
        await finish();
      } catch (e) {
        // Stale UI state: backend may already have no active cycle. Ignore and
        // proceed to create a fresh one.
        if (!(e instanceof ApiRequestError && e.status === 404)) throw e;
      }
      onCycleChange(await api.createCycle());
    } catch (e) {
      setError(e instanceof ApiRequestError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  // Progress badge on the ritual tab: how far through the eight steps we are.
  const doneCount = cycle
    ? STEPS.filter((s) => {
        const st = (cycle.steps[s.id] as StepEnvelope<unknown>).status;
        return st === "done" || st === "skipped";
      }).length
    : 0;

  const tabs = (
    <Tabs
      tabs={[
        { id: "portfolio", label: "Portfolio", icon: "◈", hint: "Live positions & capital" },
        {
          id: "ritual",
          label: "Friday Ritual",
          icon: "◷",
          hint: cycle ? `Cycle ${cycle.cycle_id}` : "Not started",
          badge: cycle ? `${doneCount}/${STEPS.length}` : undefined,
        },
        { id: "history", label: "History", icon: "▤", hint: "Past cycles, read-only" },
        { id: "backtest", label: "Backtest", icon: "◧", hint: "Research — separate data" },
      ]}
      active={tab}
      onChange={(id) => setTab(id as TabId)}
    />
  );

  if (tab !== "ritual") {
    return (
      <div className="dashboard">
        {tabs}
        <section className="tab-panel">
          {tab === "portfolio" ? <CurrentPortfolioPanel />
            : tab === "history" ? <HistoryPanel currentCycleId={cycle?.cycle_id} />
              : <BacktestPanel />}
        </section>
      </div>
    );
  }

  if (noActiveCycle || !cycle) {
    return (
      <div className="dashboard">
        {tabs}
        <div className="empty-state">
          <h2>No active cycle</h2>
          <p className="muted">Start the Friday ritual to evaluate this week's regime gate.</p>
          <button className="primary" onClick={startCycle} disabled={busy}>
            {busy ? "Starting…" : "Start Friday Ritual"}
          </button>
          {error && <p className="error-box">{error}</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard">
      {tabs}

      <div className="cycle-meta">
        <span className="cycle-id">Cycle {cycle.cycle_id}</span>
        <span className={`pill pill-${cycle.status}`}>{cycle.status}</span>
        {cycle.outcome && <span className="pill">{cycle.outcome.replace(/_/g, " ")}</span>}
        <span className="cycle-actions">
          <button className="ghost danger" onClick={deleteAndStartNew} disabled={busy}
                  title="Discard this cycle: removes the file and its journal entry">
            {busy ? "…" : "Delete & start new"}
          </button>
          <button className="ghost" onClick={closeAndStartNew} disabled={busy}
                  title="Close this cycle, keeping it as read-only history">
            {busy ? "…" : "Close & start new cycle"}
          </button>
        </span>
      </div>

      {error && <p className="error-box">{error}</p>}

      <div className="accordion">
        {STEPS.map((s, i) => {
          const env = cycle.steps[s.id] as StepEnvelope<unknown>;
          const isCurrent = cycle.current_step === s.id;
          return (
            <AccordionStep
              key={s.id}
              index={i + 1}
              title={s.title}
              status={env.status}
              current={isCurrent}
              open={openId === s.id}
              onToggle={() => setOpenId((cur) => (cur === s.id ? "" : s.id))}
            >
              {s.id === "part_a_regime_gate" ? (
                <RegimeGatePanel cycle={cycle} onCycleChange={onCycleChange} />
              ) : s.id === "part_b_scanner" ? (
                <ScannerPanel cycle={cycle} onCycleChange={onCycleChange} />
              ) : s.id === "part_c_exit_flags" ? (
                <PortfolioAuditPanel cycle={cycle} onCycleChange={onCycleChange} />
              ) : s.id === "part_d_fundamental" ? (
                <FundamentalPanel cycle={cycle} onCycleChange={onCycleChange} />
              ) : s.id === "part_d_technical" ? (
                <TechnicalPanel cycle={cycle} onCycleChange={onCycleChange} />
              ) : s.id === "part_d_risk_caps" ? (
                <RiskCapsPanel cycle={cycle} onCycleChange={onCycleChange} />
              ) : s.id === "part_e_journal" ? (
                <JournalPanel cycle={cycle} onCycleChange={onCycleChange} />
              ) : (
                <MondayPlanPanel cycle={cycle} onCycleChange={onCycleChange} />
              )}
            </AccordionStep>
          );
        })}
      </div>
    </div>
  );
}
