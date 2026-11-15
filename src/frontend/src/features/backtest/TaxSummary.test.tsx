import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { TaxBlock } from "../../api/types";
import { TaxSummary } from "./TaxSummary";

const side = (cagr: number, end = 5_000_000, exit = 100_000) => ({
  exit_tax: exit, end_equity_after_exit: end, cagr_after_exit_pct: cagr,
});

const BLOCK: TaxBlock = {
  enabled: true,
  total_tax_paid: 1_234_567,
  capital_gains_tax: 1_200_000,
  cash_income_tax: 34_567,
  by_year: [
    { fy: "2024-25", short_term_gain: 500_000, long_term_gain: 0,
      capital_gains_tax: 100_000, cash_income_tax: 3_000 },
    { fy: "2025-26", short_term_gain: -200_000, long_term_gain: 250_000,
      capital_gains_tax: 0, cash_income_tax: 4_000 },
  ],
  strategy: side(15.5),
  benchmark: side(14.0, 4_000_000, 250_000),
};

const show = (tax: TaxBlock | undefined, label = "NIFTY MIDSMALLCAP 400 TRI") =>
  render(<TaxSummary tax={tax} benchmarkLabel={label} />);

describe("TaxSummary — pre-tax runs", () => {
  it.each([
    ["no tax block at all (a run from before v2.3)", undefined],
    ["an empty block", {}],
    ["tax switched off", { enabled: false }],
    ["an incomplete block", { enabled: true, strategy: side(10) }],
    ["a block missing the strategy side", { enabled: true, benchmark: side(10) }],
  ])("warns instead of showing figures for %s", (_name, tax) => {
    show(tax as TaxBlock | undefined);

    expect(screen.getByText("Pre-tax run.")).toBeTruthy();
    expect(screen.getByText(/overstate what you would keep/)).toBeTruthy();
    expect(screen.queryByText("After tax, both sold on the last day")).toBeNull();
  });
});

describe("TaxSummary — after-tax runs", () => {
  it("puts the strategy and the benchmark side by side", () => {
    show(BLOCK);

    expect(screen.getByText("After tax, both sold on the last day")).toBeTruthy();
    expect(screen.getByText("15.5%")).toBeTruthy();
    expect(screen.getByText("14%")).toBeTruthy();
    expect(screen.getByText("NIFTY MIDSMALLCAP 400 TRI after tax")).toBeTruthy();
    expect(screen.getByText("final ₹50,00,000")).toBeTruthy();
    expect(screen.getByText("exit tax ₹2,50,000")).toBeTruthy();
    expect(screen.queryByText("Pre-tax run.")).toBeNull();
  });

  it("marks a lead over the benchmark as ahead", () => {
    show(BLOCK);
    const edge = screen.getByText("+1.50 pts");

    expect(edge.className).toContain("v-pass");
    expect(screen.getByText("ahead of holding the benchmark")).toBeTruthy();
  });

  it("marks a shortfall as behind", () => {
    show({ ...BLOCK, strategy: side(12.0) });
    const edge = screen.getByText("-2.00 pts");

    expect(edge.className).toContain("v-strike");
    expect(screen.getByText("behind holding the benchmark")).toBeTruthy();
  });

  it("treats a dead heat as not behind, with no plus sign", () => {
    show({ ...BLOCK, strategy: side(14.0) });

    expect(screen.getByText("0.00 pts").className).toContain("v-pass");
  });

  it("reports the tax paid during the run and its split", () => {
    show(BLOCK);

    expect(screen.getByText("₹12,34,567")).toBeTruthy();
    expect(screen.getByText("gains ₹12,00,000 · parked cash ₹34,567")).toBeTruthy();
  });

  it("lists each financial year", () => {
    show(BLOCK);
    const rows = screen.getAllByRole("row");

    expect(rows).toHaveLength(3);                       // header + two years
    const first = within(rows[1]);
    expect(first.getByText("2024-25")).toBeTruthy();
    expect(first.getByText("₹5,00,000")).toBeTruthy();
    expect(first.getByText("₹1,00,000")).toBeTruthy();
    expect(within(rows[2]).getByText("₹-2,00,000")).toBeTruthy();
  });

  it("leaves the yearly table out when there are no years", () => {
    show({ ...BLOCK, by_year: [] });

    expect(screen.queryByRole("table")).toBeNull();
  });

  it("copes with a block that carries no totals or years", () => {
    show({ enabled: true, strategy: side(10), benchmark: side(9) });

    expect(screen.getByText("gains — · parked cash —")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });
});
