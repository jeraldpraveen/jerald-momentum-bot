import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listBacktestUniverses: vi.fn(),
  listBacktestRuns: vi.fn(),
  getRegimeOptions: vi.fn(),
  listAltAssets: vi.fn(),
  getIndexCoverage: vi.fn(),
  getUniverseOptions: vi.fn(),
  listBenchmarks: vi.fn(),
  startBacktest: vi.fn(),
}));

vi.mock("../../api/client", () => ({
  api,
  ApiRequestError: class ApiRequestError extends Error {},
}));
vi.mock("./SweepPanel", () => ({ SweepPanel: () => <div>sweep panel</div> }));
vi.mock("./RunResult", () => ({ RunResult: () => <div>run result</div> }));

import { BacktestPanel } from "./BacktestPanel";

const TRI = {
  id: "midsmall400_tri", label: "NIFTY MIDSMALLCAP 400 TRI", source: "manual",
  total_return: true, note: "the fair comparison", available: false,
  first_session: null, last_session: null,
  how_to: "Download from niftyindices.com; save it as midsmall400_tri.csv.",
};

beforeEach(() => {
  vi.clearAllMocks();
  api.listBacktestUniverses.mockResolvedValue([
    { universe_id: "midsmall400", size: 400, microcap_count: 0, cached_prices: 400,
      first_session: "2005-01-03", last_session: "2026-07-24" }]);
  api.listBacktestRuns.mockResolvedValue([]);
  api.getRegimeOptions.mockResolvedValue({
    indices: [{ id: "nifty500", label: "NIFTY 500", ticker: "^CRSLDX", kind: "index", note: "" }],
    filters: [{ id: "st_1_2_5", label: "Supertrend (1, 2.5)", indicator: "supertrend" }],
    default_index_id: "nifty500", default_filter_id: "st_1_2_5" });
  api.listAltAssets.mockResolvedValue([]);
  api.getIndexCoverage.mockResolvedValue([]);
  api.getUniverseOptions.mockResolvedValue([]);
  api.listBenchmarks.mockResolvedValue([TRI]);
  api.startBacktest.mockResolvedValue({});
});

async function ready() {
  render(<BacktestPanel />);
  await screen.findByText(/NIFTY MIDSMALLCAP 400 TRI — total return/);
  await waitFor(() =>
    expect((screen.getByText("Run backtest") as HTMLButtonElement).disabled).toBe(false));
}

describe("BacktestPanel — Spec v2.3 run options", () => {
  it("starts a run with the v2.3 defaults: after tax, cash parked at 6.5%, regime-index benchmark", async () => {
    await ready();

    expect((screen.getByLabelText("Apply tax") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByDisplayValue("6.5")).toBeTruthy();

    fireEvent.click(screen.getByText("Run backtest"));

    await waitFor(() => expect(api.startBacktest).toHaveBeenCalledTimes(1));
    expect(api.startBacktest.mock.calls[0][0]).toMatchObject({
      universe_id: "midsmall400", apply_tax: true, cash_yield_pct: 6.5, benchmark_id: null,
    });
  });

  it("sends the chosen benchmark and the tax switch", async () => {
    await ready();

    fireEvent.change(screen.getByLabelText("Benchmark"), { target: { value: "midsmall400_tri" } });
    fireEvent.click(screen.getByLabelText("Apply tax"));

    expect(screen.getByRole("alert").textContent).toBe(TRI.how_to);   // file still to download

    fireEvent.click(screen.getByText("Run backtest"));
    await waitFor(() => expect(api.startBacktest).toHaveBeenCalledTimes(1));
    expect(api.startBacktest.mock.calls[0][0]).toMatchObject({
      benchmark_id: "midsmall400_tri", apply_tax: false,
    });
  });

  it("still renders when the benchmark list cannot be loaded", async () => {
    api.listBenchmarks.mockRejectedValue(new Error("offline"));
    render(<BacktestPanel />);

    await waitFor(() => expect(api.listBenchmarks).toHaveBeenCalled());
    expect((screen.getByLabelText("Benchmark") as HTMLSelectElement).options).toHaveLength(1);
  });

  it("says idle cash is parked in a liquid fund under the live rule", async () => {
    await ready();

    expect(screen.getByText(/6\.5% = the live rule since Spec v2\.3/)).toBeTruthy();
  });
});
