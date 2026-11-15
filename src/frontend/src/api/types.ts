// Hand-written mirror of the backend Pydantic schemas for the Part A slice.
// Switch to shared/generated/schema.d.ts (npm run gen:api) once Part B lands.

export type StepStatus = "pending" | "in_progress" | "done" | "skipped" | "failed";
export type Regime = "GREEN" | "RED";
export type CycleStatus = "open" | "closed";
export type CycleOutcome = "red_terminated" | "green_completed";

export type StepId =
  | "part_a_regime_gate"
  | "part_b_scanner"
  | "part_c_exit_flags"
  | "part_d_fundamental"
  | "part_d_technical"
  | "part_d_risk_caps"
  | "part_e_journal"
  | "phase3_plan";

export interface RegimeGateOutput {
  regime: Regime;
  week_end: string;
  index_close: number;
  line_label: string;
  line_value: number | null;
  index_id: string;
  index_label: string;
  index_ticker: string;
  filter_id: string;
  filter_label: string;
  indicator: string;
  data_source: string;
  // False when the gate ran on an index other than the ratified one (v2.3).
  ratified_index?: boolean;
  // False when the candle judged is not for the cycle's own week (stale feed).
  week_matches_cycle?: boolean;
  previous_regime: Regime | null;
  reentry_required: boolean;
  actions: string[];
  active_holdings: string[];
  pending_exit: string[];
}

export interface IndexOption {
  id: string;
  label: string;
  ticker: string;
  kind: string;
  note: string;
}

export interface FilterOption {
  id: string;
  label: string;
  indicator: string;
}

export interface RegimeOptions {
  indices: IndexOption[];
  filters: FilterOption[];
  default_index_id: string;
  default_filter_id: string;
}

export interface StepEnvelope<T> {
  status: StepStatus;
  started_at: string | null;
  completed_at: string | null;
  input: Record<string, unknown> | null;
  output: T | null;
  error: string | null;
  notes: string | null;
}

export interface FormulaValidateResponse {
  ok: boolean;
  error: string | null;
  terms: string[];
  frozen?: boolean;
}

// The one formula the Friday ritual scores with (Spec v2.3).
export interface FrozenFormula {
  formula: string;
  since: string;
  reason: string;
}

export interface ScannerFilters {
  sectors: string[];
  price_min: number | null;
  price_max: number | null;
  as_of: string | null;
}

export interface ScannerCandidate {
  symbol: string;
  company: string;
  sector: string;
  close: number | null;
  median_tv_cr: number | null;
  listed_days: number;
  score: number | null;
  rank: number | null;
  eligible: boolean;
  flags: string[];
}

export interface ScannerOutput {
  universe_ids: string[];
  universe_label: string;
  as_of: string;
  formula: string;
  filters: ScannerFilters;
  total: number;
  eligible_count: number;
  candidates: ScannerCandidate[];
}

export interface UniverseOption {
  id: string;
  label: string;
  note: string;
}

export type HoldingStatus = "ACTIVE" | "PENDING_EXIT" | "EXITED";

export interface Holding {
  symbol: string;
  qty: number | null;
  entry_price: number | null;
  entry_date: string;
  stop_price: number | null;
  sleeve: string;
  sector: string;
  status: HoldingStatus;
  exit_reason: string;
  marked_on: string;
  exit_price: number | null;
  entry_charges: number | null;
  exit_charges: number | null;
}

export interface PositionValue {
  symbol: string;
  status: HoldingStatus;
  qty: number | null;
  entry_price: number | null;
  cost: number | null;
  last_close: number | null;
  price_date: string;
  market_value: number | null;
  pnl: number | null;
  pnl_pct: number | null;
  weight_pct: number | null;
  stop_price: number | null;
  risk: number | null;
}

export interface PortfolioSummary {
  as_of: string;
  price_date: string;
  held_count: number;
  active_count: number;
  pending_exit_count: number;
  exited_count: number;
  capital_deployed: number | null;
  market_value: number | null;
  unrealised_pnl: number | null;
  unrealised_pnl_pct: number | null;
  open_risk: number | null;
  realised_pnl: number | null;
  realised_pnl_pct: number | null;
  realised_count: number;
  positions: PositionValue[];
  incomplete: string[];
  unpriced: string[];
  stale_prices: string[];
  warnings: string[];
}

export interface SymbolLookup {
  symbol: string;
  found: boolean;
  sector: string;
  sleeve: string;
  last_close: number | null;
}

// --- Step-4: fundamental veto ---
export type CheckVerdict = "pass" | "strike" | "unverified" | "n/a";

export interface FundamentalAssessment {
  pledging_pct: number | null;
  interest_coverage: number | null;
  pending_action: boolean | null;
  note: string;
}

export interface CheckResult {
  verdict: CheckVerdict;
  value: number | null;
  detail: string;
}

export interface FundamentalCandidate {
  symbol: string;
  rank: number;
  score: number | null;
  sector: string;
  listed_days: number;
  is_financial: boolean;
  auto_interest_coverage: number | null;
  surveillance: string;
  assessment: FundamentalAssessment;
  pledging: CheckResult;
  interest_cover: CheckResult;
  pending_action: CheckResult;
  listing_age: CheckResult;
  struck: boolean;
  strikes: string[];
  flags: string[];
}

export interface FundamentalOutput {
  as_of: string;
  rank_window: number;
  candidates: FundamentalCandidate[];
  struck_count: number;
  passed_count: number;
  // False when NSE's ASM/GSM lists could not be fetched, so check 6 did not run.
  surveillance_checked?: boolean;
  actions: string[];
}

// --- Step-5: technical veto ---
export interface TechnicalCandidate {
  symbol: string;
  rank: number;
  sector: string;
  close: number | null;
  dma_50: number | null;
  dma_20: number | null;
  extension_pct: number | null;
  rsi_14: number | null;
  trend: CheckResult;
  extension: CheckResult;
  overbought: CheckResult;
  struck: boolean;
  strikes: string[];
  flags: string[];
}

export interface TechnicalOutput {
  as_of: string;
  candidates: TechnicalCandidate[];
  struck_count: number;
  passed_count: number;
  survivors: string[];
  actions: string[];
}

// --- Step-6: risk caps & slot filling ---
export interface PortfolioSlot {
  symbol: string;
  sector: string;
  sleeve: string;
}

export interface SelectedBuy {
  symbol: string;
  rank: number;
  sector: string;
  sleeve: string;
  score: number | null;
  weight_pct: number;
}

export interface SkippedCandidate {
  symbol: string;
  rank: number;
  sector: string;
  sleeve: string;
  reason: string;
}

export interface RiskCapsOutput {
  as_of: string;
  target_min: number;
  target_max: number;
  max_per_sector: number;
  max_microcap: number;
  kept: PortfolioSlot[];
  open_slots: number;
  buys: SelectedBuy[];
  skipped: SkippedCandidate[];
  unfilled_slots: number;
  sector_counts: Record<string, number>;
  microcap_count: number;
  final_portfolio_size: number;
  // False when the Microcap 250 list could not be loaded: the cap was NOT applied.
  microcap_cap_enforced?: boolean;
  actions: string[];
}

// --- Step-7: journal finalize ---
export interface JournalHolding {
  symbol: string;
  rank: number | null;
  action: string;
  reason: string;
}

export interface JournalBuy {
  symbol: string;
  rank: number;
  sector: string;
  weight_pct: number;
}

export interface JournalStrikeout {
  symbol: string;
  rank: number;
  step: string;
  reasons: string[];
}

export interface JournalEntry {
  cycle_id: string;
  date: string;
  logged_at: string;
  regime: string;
  regime_detail: string;
  holdings: JournalHolding[];
  exits: string[];
  buys: JournalBuy[];
  strikeouts: JournalStrikeout[];
  unfilled_slots: number;
  final_portfolio_size: number;
  stops_verified: boolean | null;
  notes: string;
}

export interface JournalOutput {
  entry: JournalEntry;
  committed: boolean;
  actions: string[];
}

// --- Step-8: Monday execution plan ---
export interface ExitOrder {
  symbol: string;
  qty: number | null;
  existing_stop: number | null;
  last_close: number | null;
  est_proceeds: number | null;
  reason: string;
}

export interface BuyOrder {
  symbol: string;
  rank: number;
  sector: string;
  target_weight_pct: number;
  target_value: number | null;
  last_close: number | null;
  indicative_qty: number | null;
  indicative_stop: number | null;
  funded: boolean;
  defer_reason: string;
}

export interface Phase3Output {
  as_of: string;
  regime: string;
  capital_basis: number | null;
  capital_source: string;
  available_cash: number | null;
  cash_source: string;
  deployable: number | null;
  leftover_cash: number | null;
  funded_count: number;
  deferred_count: number;
  stop_pct: number;
  exits: ExitOrder[];
  buys: BuyOrder[];
  est_proceeds: number | null;
  est_deployment: number | null;
  // Spec v2.3 parking: what goes into the liquid fund Monday, and what must
  // come back out of it first to fund the buys.
  parking_instrument?: string;
  park_amount?: number | null;
  redeem_amount?: number | null;
  checklist: string[];
  warnings: string[];
  actions: string[];
}

export interface HoldingAudit {
  symbol: string;
  sector: string;
  rank: number | null;
  score: number | null;
  in_universe: boolean;
  exit_flagged: boolean;
  reasons: string[];
}

export interface ExitFlagsOutput {
  as_of: string;
  rank_threshold: number;
  universe_label: string;
  holdings: HoldingAudit[];
  exit_count: number;
  keep_count: number;
  actions: string[];
}

export interface CycleSteps {
  part_a_regime_gate: StepEnvelope<RegimeGateOutput>;
  part_b_scanner: StepEnvelope<ScannerOutput>;
  part_c_exit_flags: StepEnvelope<ExitFlagsOutput>;
  part_d_fundamental: StepEnvelope<FundamentalOutput>;
  part_d_technical: StepEnvelope<TechnicalOutput>;
  part_d_risk_caps: StepEnvelope<RiskCapsOutput>;
  part_e_journal: StepEnvelope<JournalOutput>;
  phase3_plan: StepEnvelope<Phase3Output>;
}

export interface RegimeDecision {
  index: string;
  filter: string;
  regime: Regime;
}

export interface Cycle {
  cycle_id: string;
  created_at: string;
  updated_at: string;
  status: CycleStatus;
  current_step: StepId | "done";
  outcome: CycleOutcome | null;
  regime_decision: RegimeDecision | null;
  steps: CycleSteps;
  intraday_events: unknown[];
}

export interface CycleSummary {
  cycle_id: string;
  status: CycleStatus;
  outcome: CycleOutcome | null;
  current_step: string;
  created_at: string;
  updated_at: string;
}

export interface ApiError {
  error: string;
  message: string;
  [k: string]: unknown;
}

// --- Backtest (research module — separate flow from the Friday ritual) ---
export type RunStatus =
  | "queued" | "fetching" | "running" | "done" | "failed" | "cancelled" | "interrupted";

export interface UniverseSummary {
  universe_id: string;
  size: number;
  microcap_count: number;
  cached_prices: number;
  first_session: string | null;
  last_session: string | null;
}

export interface RunProgress {
  done: number;
  total: number;
  pct: number;
  detail: string;
}

export interface BacktestMetrics {
  start_equity: number;
  end_equity: number;
  total_return_pct: number;
  cagr_pct: number;
  volatility_pct: number;
  sharpe: number;
  sortino: number;
  max_drawdown_pct: number;
  max_drawdown_weeks: number;
  weeks: number;
  exposure_pct: number;
  win_rate_pct: number;
  win_rate_invested_pct: number;
}

export interface RunSummary {
  metrics: BacktestMetrics;
  benchmark: BacktestMetrics;
  trade_stats: Record<string, number>;
  turnover: number;
  config: Record<string, unknown>;
}

export interface RunRecord {
  run_id: string;
  label: string;
  status: RunStatus;
  created_at: string;
  updated_at: string;
  request: Record<string, unknown>;
  progress: RunProgress;
  summary: RunSummary | null;
  error: string;
}

export interface EquityPoint {
  date: string;
  equity: number;
  cash?: number;
  positions?: number;
  regime?: string;
}

export interface BacktestWeek {
  week_end: string;
  regime: string;
  line_label: string;
  line_value: number | null;
  exits: string[];
  buys: string[];
  stops_hit: string[];
  held: string[];
  equity: number;
  eligible: number;
  deferred?: string[];      // selected, but not enough cash for a full position
  unfilled_buys?: string[];
  unfilled_exits?: string[];
  blocked_buys?: string[];
}

export interface BacktestTrade {
  date: string;
  symbol: string;
  side: string;
  qty: number;
  price: number;
  value: number;
  charges: number;
  reason: string;
  pnl?: number | null;
  tax?: number;
}

// One side of the after-tax comparison: sold in full on the last day.
export interface TaxSide {
  exit_tax: number;
  end_equity_after_exit: number;
  cagr_after_exit_pct: number;
}

export interface TaxYear {
  fy: string;
  short_term_gain: number;
  long_term_gain: number;
  capital_gains_tax: number;
  cash_income_tax: number;
}

// `enabled: false` (or an empty object on runs from before v2.3) = pre-tax.
export interface TaxBlock {
  enabled?: boolean;
  total_tax_paid?: number;
  capital_gains_tax?: number;
  cash_income_tax?: number;
  losses_carried_forward?: number;
  rules?: Record<string, number>;
  by_year?: TaxYear[];
  strategy?: TaxSide;
  benchmark?: TaxSide;
}

export interface BenchmarkOption {
  id: string;
  label: string;
  source: string;          // "manual" (a downloaded file) | "index"
  total_return: boolean;
  note: string;
  available: boolean;
  first_session: string | null;
  last_session: string | null;
  how_to: string;
}

export interface BacktestResult {
  run_id: string;
  config: Record<string, unknown>;
  metrics: BacktestMetrics;
  benchmark: BacktestMetrics;
  tax?: TaxBlock;
  trade_stats: Record<string, number>;
  turnover: number;
  equity_curve: EquityPoint[];
  benchmark_curve: EquityPoint[];
  weeks: BacktestWeek[];
  trades: BacktestTrade[];
  limitations: string[];
}

export interface IndexCoverage {
  id: string;
  label: string;
  ticker: string;
  first_session: string | null;
  last_session: string | null;
  first_tradable: string | null;
  cached: boolean;
}

export interface AltAssetOption {
  id: string;
  label: string;
  ticker: string;
  note: string;
  first_session: string;
}

export interface BacktestRunRequest {
  universe_id: string;
  label?: string;
  index_id?: string;
  filter_id?: string;
  formula?: string;
  start?: string | null;
  end?: string | null;
  starting_capital?: number;
  position_weight_pct?: number;
  stop_multiplier?: number;
  cost_bps?: number;
  slippage_bps?: number;
  rank_exit_threshold?: number | null;
  veto_rank_window?: number | null;
  alt_asset_id?: string | null;
  alt_allocation_pct?: number;
  cash_yield_pct?: number;
  benchmark_id?: string | null;
  apply_tax?: boolean;
  fetch_prices?: boolean;
}

// --- Formula sweeps (walk-forward optimisation) ---
export interface NoiseFloor {
  n: number;
  min_cagr: number;
  max_cagr: number;
  mean_cagr: number;
  std_cagr: number;
  spread: number;
}

// Every field here ships with every winner, on purpose: the bare CAGR is the
// misleading headline, and these are what say whether selection did anything.
export interface SweepVerdict {
  baseline_formula: string;
  baseline_train_cagr: number;
  baseline_test_cagr: number;
  winner_formula: string;
  winner_train_cagr: number;
  winner_test_cagr: number;
  winner_test_percentile: number;
  winner_train_edge: number;
  winner_test_edge: number;
  pct_beating_baseline_test: number;
  median_test_cagr: number;
  best_possible_test_cagr: number;
  train_test_corr: number;
  formulas_tried: number;
}

export interface SweepRecord {
  sweep_id: string;
  label: string;
  status: RunStatus;
  created_at: string;
  updated_at: string;
  request: Record<string, unknown>;
  progress: RunProgress;
  noise: NoiseFloor | null;
  verdict: SweepVerdict | null;
  error: string;
}

export interface SweepResultRow {
  formula: string;
  train_cagr: number;
  train_sharpe: number;
  test_cagr: number;
  test_sharpe: number;
  test_maxdd: number;
  test_turnover: number;
}

export interface SweepResult {
  sweep_id: string;
  config: Record<string, unknown>;
  noise: NoiseFloor;
  verdict: SweepVerdict;
  grid: SweepResultRow[];
  limitations: string[];
}

export interface SweepRunRequest {
  universe_id: string;
  label?: string;
  index_id?: string;
  filter_id?: string;
  train_end?: string;
  test_start?: string;
  weight_step?: number;
  noise_samples?: number;
  cost_bps?: number;
  slippage_bps?: number;
}
