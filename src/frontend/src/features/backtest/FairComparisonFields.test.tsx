import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { BenchmarkOption } from "../../api/types";
import { FairComparisonFields, benchmarkOptionLabel } from "./FairComparisonFields";

const opt = (over: Partial<BenchmarkOption>): BenchmarkOption => ({
  id: "x", label: "X", source: "index", total_return: false, note: "a note",
  available: true, first_session: "2005-01-03", last_session: "2026-07-24", how_to: "",
  ...over,
});

const TRI_MISSING = opt({
  id: "midsmall400_tri", label: "NIFTY MIDSMALLCAP 400 TRI", source: "manual",
  total_return: true, available: false, note: "the fair comparison",
  how_to: "Download from niftyindices.com; save it as midsmall400_tri.csv.",
});
const TRI_READY = opt({ ...TRI_MISSING, id: "nifty500_tri", label: "NIFTY 500 TRI",
                        available: true, how_to: "", note: "broad market with dividends" });
const PRICE = opt({ id: "nifty500", label: "NIFTY 500", note: "PRICE index — dividends are excluded" });
const UNCACHED = opt({ id: "banknifty", label: "NIFTY BANK", available: false });

const ALL = [TRI_MISSING, TRI_READY, PRICE, UNCACHED];

function setup(props: Partial<Parameters<typeof FairComparisonFields>[0]> = {}) {
  const onBenchmark = vi.fn();
  const onApplyTax = vi.fn();
  render(<FairComparisonFields benchmarks={ALL} benchmarkId={null} applyTax
                               onBenchmark={onBenchmark} onApplyTax={onApplyTax} {...props} />);
  return { onBenchmark, onApplyTax };
}

describe("benchmarkOptionLabel", () => {
  it("says whether dividends are included", () => {
    expect(benchmarkOptionLabel(TRI_READY)).toBe("NIFTY 500 TRI — total return");
    expect(benchmarkOptionLabel(PRICE)).toBe("NIFTY 500 — price only");
  });

  it("says when the data is not on disk, and why", () => {
    expect(benchmarkOptionLabel(TRI_MISSING)).toBe(
      "NIFTY MIDSMALLCAP 400 TRI — total return · file missing");
    expect(benchmarkOptionLabel(UNCACHED)).toBe("NIFTY BANK — price only · not cached");
  });
});

describe("FairComparisonFields", () => {
  it("defaults to the regime index and explains why that flatters the strategy", () => {
    setup();
    const select = screen.getByLabelText("Benchmark") as HTMLSelectElement;

    expect(select.value).toBe("");
    expect(select.options).toHaveLength(ALL.length + 1);
    expect(select.options[0].textContent).toBe("Regime index (default)");
    expect(screen.getByText(/leaves out dividends/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the chosen benchmark's note", () => {
    setup({ benchmarkId: "nifty500_tri" });

    expect((screen.getByLabelText("Benchmark") as HTMLSelectElement).value).toBe("nifty500_tri");
    expect(screen.getByText("broad market with dividends")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("tells you where to get a total-return file that is missing", () => {
    setup({ benchmarkId: "midsmall400_tri" });

    expect(screen.getByRole("alert").textContent).toBe(TRI_MISSING.how_to);
  });

  it("raises no download alert for an index that is merely uncached", () => {
    setup({ benchmarkId: "banknifty" });

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("treats an id that is not in the list as the default", () => {
    setup({ benchmarkId: "gone" });

    expect(screen.getByText(/leaves out dividends/)).toBeTruthy();
  });

  it("reports the benchmark picked, and null for the default", () => {
    const { onBenchmark } = setup();
    const select = screen.getByLabelText("Benchmark");

    fireEvent.change(select, { target: { value: "midsmall400_tri" } });
    expect(onBenchmark).toHaveBeenLastCalledWith("midsmall400_tri");

    fireEvent.change(select, { target: { value: "" } });
    expect(onBenchmark).toHaveBeenLastCalledWith(null);
  });

  it("starts with tax applied and states the rates", () => {
    setup();

    expect((screen.getByLabelText("Apply tax") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/short-term 20%, long-term 12.5%/)).toBeTruthy();
  });

  it("reports the tax toggle and warns when it is off", () => {
    const { onApplyTax } = setup({ applyTax: false });
    const box = screen.getByLabelText("Apply tax") as HTMLInputElement;

    expect(box.checked).toBe(false);
    expect(screen.getByText(/pre-tax — overstates/)).toBeTruthy();

    fireEvent.click(box);
    expect(onApplyTax).toHaveBeenCalledWith(true);
  });

  it("renders with no benchmarks loaded yet", () => {
    setup({ benchmarks: [] });

    expect((screen.getByLabelText("Benchmark") as HTMLSelectElement).options).toHaveLength(1);
  });
});
