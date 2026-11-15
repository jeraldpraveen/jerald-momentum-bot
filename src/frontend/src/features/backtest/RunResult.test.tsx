import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BacktestMetrics, BacktestResult } from "../../api/types";

const api = vi.hoisted(() => ({ getBacktestResult: vi.fn() }));

vi.mock("../../api/client", () => ({
  api,
  ApiRequestError: class ApiRequestError extends Error {},
}));

// The chart is drawn on an SVG that jsdom cannot measure; what matters here is
// which series it is handed.
vi.mock("../../components/LineChart", () => ({
  LineChart: ({ ariaLabel, series }: { ariaLabel: string; series: { name: string }[] }) => (
    <div aria-label={ariaLabel}>{series.map((s) => s.name).join(" | ")}</div>
  ),
}));

import { RunResult } from "./RunResult";

const metrics = (cagr: number): BacktestMetrics => ({
  start_equity: 1_000_000, end_equity: 2_000_000, total_return_pct: 100, cagr_pct: cagr,
  volatility_pct: 20, sharpe: 1, sortino: 1.2, max_drawdown_pct: -30, max_drawdown_weeks: 40,
  weeks: 100, exposure_pct: 60, win_rate_pct: 40, win_rate_invested_pct: 58,
});

function result(over: Partial<BacktestResult> = {}): BacktestResult {
  return {
    run_id: "r1",
    config: {
      universe_id: "midsmall400", start: "2020-01-03", end: "2026-07-24",
      filter_label: "Supertrend (1, 2.5)", index_label: "NIFTY 500", universe_size: 400,
      benchmark_label: "NIFTY MIDSMALLCAP 400 TRI",
    },
    metrics: metrics(18), benchmark: metrics(15),
    tax: {
      enabled: true, total_tax_paid: 500_000, capital_gains_tax: 480_000,
      cash_income_tax: 20_000, by_year: [],
      strategy: { exit_tax: 10_000, end_equity_after_exit: 1_990_000, cagr_after_exit_pct: 14.5 },
      benchmark: { exit_tax: 90_000, end_equity_after_exit: 1_910_000, cagr_after_exit_pct: 13.9 },
    },
    trade_stats: { closed_trades: 10, win_rate_pct: 50 },
    turnover: 6,
    equity_curve: [{ date: "2020-01-03", equity: 1_000_000, cash: 10, positions: 3, regime: "GREEN" }],
    benchmark_curve: [{ date: "2020-01-03", equity: 1_000_000 }],
    weeks: [], trades: [], limitations: ["Fixed universe — survivorship bias."],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("RunResult — Spec v2.3 additions", () => {
  it("names the benchmark the run was measured against, everywhere it appears", async () => {
    api.getBacktestResult.mockResolvedValue(result());
    render(<RunResult runId="r1" />);

    expect(await screen.findByText("Benchmark: buy and hold NIFTY MIDSMALLCAP 400 TRI."))
      .toBeTruthy();
    expect(screen.getByText("benchmark 15%")).toBeTruthy();
    expect(screen.getByLabelText(
      "Strategy equity versus buy-and-hold NIFTY MIDSMALLCAP 400 TRI").textContent)
      .toBe("Strategy | NIFTY MIDSMALLCAP 400 TRI");
    expect(screen.getByText("Buy & hold NIFTY MIDSMALLCAP 400 TRI")).toBeTruthy();

    fireEvent.click(screen.getByText("Data"));
    expect(screen.getByRole("columnheader", { name: "NIFTY MIDSMALLCAP 400 TRI" })).toBeTruthy();
  });

  it("shows the after-tax comparison under the headline", async () => {
    api.getBacktestResult.mockResolvedValue(result());
    render(<RunResult runId="r1" />);

    expect(await screen.findByText("After tax, both sold on the last day")).toBeTruthy();
    expect(screen.getByText("NIFTY MIDSMALLCAP 400 TRI after tax")).toBeTruthy();
    expect(screen.getByText("+0.60 pts")).toBeTruthy();
  });

  it("falls back to the regime index and a pre-tax warning for an older run", async () => {
    const old = result({ tax: undefined });
    delete (old.config as Record<string, unknown>).benchmark_label;
    api.getBacktestResult.mockResolvedValue(old);
    render(<RunResult runId="r1" />);

    expect(await screen.findByText("Benchmark: buy and hold NIFTY 500.")).toBeTruthy();
    expect(screen.getByText("Pre-tax run.")).toBeTruthy();
    expect(screen.getByText("Buy & hold NIFTY 500")).toBeTruthy();
  });

  it("shows orders unfilled due to missing opens, including older results", async () => {
    api.getBacktestResult.mockResolvedValue(result({ weeks: [
      { week_end: "2020-01-03", regime: "GREEN", line_label: "", line_value: null,
        exits: [], buys: [], stops_hit: [], held: [], equity: 100, eligible: 1,
        unfilled_buys: ["AAA"], unfilled_exits: ["BBB"], blocked_buys: ["CCC"] },
      { week_end: "2020-01-10", regime: "GREEN", line_label: "", line_value: null,
        exits: [], buys: [], stops_hit: [], held: [], equity: 100, eligible: 1 },
    ] }));
    render(<RunResult runId="r1" />);
    fireEvent.click(await screen.findByRole("button", { name: "Weeks" }));
    expect(screen.getByRole("columnheader", { name: "Unfilled buys" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Unfilled exits" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Blocked buys" })).toBeTruthy();
    expect(screen.getByText("AAA")).toBeTruthy();
    expect(screen.getByText("BBB")).toBeTruthy();
    expect(screen.getByText("CCC")).toBeTruthy();
  });
});
