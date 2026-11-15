import type {
  AltAssetOption,
  ApiError,
  BacktestResult,
  BacktestRunRequest,
  BenchmarkOption,
  Cycle,
  CycleSummary,
  FormulaValidateResponse,
  FrozenFormula,
  FundamentalAssessment,
  Holding,
  IndexCoverage,
  JournalEntry,
  PortfolioSummary,
  RegimeOptions,
  RunRecord,
  ScannerFilters,
  SweepRecord,
  SweepResult,
  SweepRunRequest,
  SymbolLookup,
  UniverseOption,
  UniverseSummary,
} from "./types";

export class ApiRequestError extends Error {
  status: number;
  body: ApiError | null;
  constructor(status: number, body: ApiError | null) {
    super(body?.message ?? `Request failed (${status})`);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  if (!res.ok) {
    throw new ApiRequestError(res.status, json as ApiError);
  }
  return json as T;
}

export const api = {
  // --- Current Portfolio (holdings ledger) ---
  getHoldings: () => request<Holding[]>("/api/holdings"),

  saveHoldings: (holdings: Holding[]) =>
    request<Holding[]>("/api/holdings", {
      method: "PUT",
      body: JSON.stringify({ holdings }),
    }),

  getPortfolioSummary: (refresh = false) =>
    request<PortfolioSummary>(`/api/holdings/summary${refresh ? "?refresh=true" : ""}`),

  lookupSymbol: (symbol: string) =>
    request<SymbolLookup>(`/api/holdings/lookup?symbol=${encodeURIComponent(symbol)}`),

  getRegimeOptions: () => request<RegimeOptions>("/api/regime-options"),

  getCurrentCycle: () => request<Cycle>("/api/cycles/current"),

  listCycles: () => request<CycleSummary[]>("/api/cycles"),

  getCycle: (cycleId: string) =>
    request<Cycle>(`/api/cycles/${encodeURIComponent(cycleId)}`),

  listJournal: (limit = 50) => request<JournalEntry[]>(`/api/journal?limit=${limit}`),

  closeCycle: () => request<Cycle>("/api/cycles/current/close", { method: "POST" }),

  deleteCycle: () =>
    request<{ deleted: string }>("/api/cycles/current", { method: "DELETE" }),

  reopenStep: (stepId: string) =>
    request<Cycle>(`/api/cycles/current/steps/${stepId}/reopen`, { method: "POST" }),

  createCycle: (weekEnd?: string) =>
    request<Cycle>("/api/cycles", {
      method: "POST",
      body: JSON.stringify({ week_end: weekEnd ?? null }),
    }),

  runPartA: (indexId: string, filterId: string) =>
    request<Cycle>("/api/cycles/current/steps/part-a/run", {
      method: "POST",
      body: JSON.stringify({ index_id: indexId, filter_id: filterId }),
    }),

  advancePartA: () =>
    request<Cycle>("/api/cycles/current/steps/part-a/advance", { method: "POST" }),

  getUniverseOptions: () => request<UniverseOption[]>("/api/universe-options"),

  getUniverseSectors: (universeIds: string[]) => {
    const qs = universeIds.map((id) => `ids=${encodeURIComponent(id)}`).join("&");
    return request<string[]>(`/api/universe-sectors?${qs}`);
  },

  // `formula` is omitted on purpose: the live score is frozen (Spec v2.3) and
  // the backend refuses anything else with 409 formula_frozen.
  runPartB: (body: { universe_ids: string[]; formula?: string; filters: ScannerFilters }) =>
    request<Cycle>("/api/cycles/current/steps/part-b/run", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getFrozenFormula: () => request<FrozenFormula>("/api/scanner/frozen-formula"),

  validateFormula: (formula: string) =>
    request<FormulaValidateResponse>("/api/scanner/validate-formula", {
      method: "POST",
      body: JSON.stringify({ formula }),
    }),

  advancePartB: () =>
    request<Cycle>("/api/cycles/current/steps/part-b/advance", { method: "POST" }),

  runPartC: () =>
    request<Cycle>("/api/cycles/current/steps/part-c/run", { method: "POST" }),

  runFundamental: (assessments: Record<string, FundamentalAssessment>) =>
    request<Cycle>("/api/cycles/current/steps/part-d-fundamental/run", {
      method: "POST",
      body: JSON.stringify({ assessments }),
    }),

  advanceFundamental: () =>
    request<Cycle>("/api/cycles/current/steps/part-d-fundamental/advance", { method: "POST" }),

  runTechnical: () =>
    request<Cycle>("/api/cycles/current/steps/part-d-technical/run", { method: "POST" }),

  advanceTechnical: () =>
    request<Cycle>("/api/cycles/current/steps/part-d-technical/advance", { method: "POST" }),

  runRiskCaps: () =>
    request<Cycle>("/api/cycles/current/steps/part-d-risk-caps/run", { method: "POST" }),

  advanceRiskCaps: () =>
    request<Cycle>("/api/cycles/current/steps/part-d-risk-caps/advance", { method: "POST" }),

  runJournal: (body: { stops_verified: boolean | null; notes: string }) =>
    request<Cycle>("/api/cycles/current/steps/part-e/run", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  advanceJournal: () =>
    request<Cycle>("/api/cycles/current/steps/part-e/advance", { method: "POST" }),

  runPhase3: (body: { portfolio_capital: number | null; available_cash: number | null }) =>
    request<Cycle>("/api/cycles/current/steps/phase3/run", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  advancePhase3: () =>
    request<Cycle>("/api/cycles/current/steps/phase3/advance", { method: "POST" }),

  // --- Backtest (research; never touches the ritual) ---
  listAltAssets: () => request<AltAssetOption[]>("/api/backtest/alt-assets"),

  getIndexCoverage: () => request<IndexCoverage[]>("/api/backtest/index-coverage"),

  listBenchmarks: () => request<BenchmarkOption[]>("/api/backtest/benchmarks"),

  listBacktestUniverses: () => request<UniverseSummary[]>("/api/backtest/universes"),

  buildBacktestUniverse: (universeId: string, indexIds: string[]) =>
    request<UniverseSummary>("/api/backtest/universes", {
      method: "POST",
      body: JSON.stringify({ universe_id: universeId, index_ids: indexIds }),
    }),

  deleteBacktestUniverse: (universeId: string) =>
    request<{ deleted: string }>(`/api/backtest/universes/${encodeURIComponent(universeId)}`,
      { method: "DELETE" }),

  startBacktest: (body: BacktestRunRequest) =>
    request<RunRecord>("/api/backtest/runs", { method: "POST", body: JSON.stringify(body) }),

  listBacktestRuns: () => request<RunRecord[]>("/api/backtest/runs"),

  getBacktestRun: (runId: string) =>
    request<RunRecord>(`/api/backtest/runs/${encodeURIComponent(runId)}`),

  getBacktestResult: (runId: string) =>
    request<BacktestResult>(`/api/backtest/runs/${encodeURIComponent(runId)}/result`),

  cancelBacktest: (runId: string) =>
    request<{ cancelling: boolean }>(`/api/backtest/runs/${encodeURIComponent(runId)}/cancel`,
      { method: "POST" }),

  deleteBacktestRun: (runId: string) =>
    request<{ deleted: string }>(`/api/backtest/runs/${encodeURIComponent(runId)}`,
      { method: "DELETE" }),

  // --- formula sweeps ---
  startSweep: (body: SweepRunRequest) =>
    request<SweepRecord>("/api/backtest/sweeps", {
      method: "POST", body: JSON.stringify(body),
    }),

  listSweeps: () => request<SweepRecord[]>("/api/backtest/sweeps"),

  getSweepResult: (id: string) =>
    request<SweepResult>(`/api/backtest/sweeps/${encodeURIComponent(id)}/result`),

  cancelSweep: (id: string) =>
    request<{ cancelling: boolean }>(`/api/backtest/sweeps/${encodeURIComponent(id)}/cancel`,
      { method: "POST" }),

  deleteSweep: (id: string) =>
    request<{ deleted: string }>(`/api/backtest/sweeps/${encodeURIComponent(id)}`,
      { method: "DELETE" }),

  advancePartC: () =>
    request<Cycle>("/api/cycles/current/steps/part-c/advance", { method: "POST" }),
};
