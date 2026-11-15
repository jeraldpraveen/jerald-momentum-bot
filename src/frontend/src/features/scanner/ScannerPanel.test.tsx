import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Cycle, ScannerOutput } from "../../api/types";
import { FROZEN_FORMULA } from "./FrozenFormula";

const api = vi.hoisted(() => ({
  getUniverseOptions: vi.fn(),
  getUniverseSectors: vi.fn(),
  getFrozenFormula: vi.fn(),
  validateFormula: vi.fn(),
  runPartB: vi.fn(),
  advancePartB: vi.fn(),
}));

vi.mock("../../api/client", () => {
  class ApiRequestError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  }
  return { api, ApiRequestError };
});

vi.mock("../../components/ReopenStepButton", () => ({
  ReopenStepButton: ({ stepId }: { stepId: string }) => <span>reopen:{stepId}</span>,
}));

import { ApiRequestError } from "../../api/client";
import { ScannerPanel } from "./ScannerPanel";

const TODAY = new Date().toLocaleDateString("en-CA");

const OUTPUT: ScannerOutput = {
  universe_ids: ["midsmall400", "holdings"],
  universe_label: "NIFTY MIDSMALLCAP 400 + Current Holdings",
  as_of: "2026-11-13",
  formula: FROZEN_FORMULA,
  filters: { sectors: [], price_min: null, price_max: null, as_of: "2026-11-13" },
  total: 2,
  eligible_count: 1,
  candidates: [
    { symbol: "AAA", company: "", sector: "Auto", close: 120.5, median_tv_cr: 12.3,
      listed_days: 900, score: 4.2, rank: 1, eligible: true, flags: [] },
    { symbol: "BBB", company: "", sector: "Bank", close: null, median_tv_cr: null,
      listed_days: 10, score: null, rank: null, eligible: false,
      flags: ["illiquid", "listed<12m"] },
  ],
};

function cycle(over: { output?: ScannerOutput | null; step?: string; status?: string;
                       error?: string | null } = {}): Cycle {
  return {
    cycle_id: "2026-11-13",
    current_step: over.step ?? "part_b_scanner",
    steps: {
      part_b_scanner: {
        status: over.status ?? "pending", output: over.output ?? null,
        error: over.error ?? null,
      },
    },
  } as unknown as Cycle;
}

function setup(c: Cycle = cycle()) {
  const onCycleChange = vi.fn();
  const view = render(<ScannerPanel cycle={c} onCycleChange={onCycleChange} />);
  return { onCycleChange, ...view };
}

/** Flush the mount-time fetches so later assertions see settled state. */
const settled = () => waitFor(() => expect(api.getFrozenFormula).toHaveBeenCalled());

beforeEach(() => {
  vi.clearAllMocks();
  api.getUniverseOptions.mockResolvedValue([
    { id: "holdings", label: "Current Holdings", note: "" },
    { id: "midsmall400", label: "NIFTY MIDSMALLCAP 400", note: "" },
    { id: "microcap250", label: "NIFTY MICROCAP 250", note: "" },
  ]);
  api.getUniverseSectors.mockResolvedValue(["Auto", "Bank"]);
  api.getFrozenFormula.mockResolvedValue({
    formula: FROZEN_FORMULA, since: "Spec v2.3", reason: "server says: frozen" });
  api.runPartB.mockResolvedValue(cycle({ output: OUTPUT, status: "done" }));
  api.advancePartB.mockResolvedValue(cycle({ output: OUTPUT, step: "part_c_exit_flags" }));
});

describe("ScannerPanel — the frozen formula", () => {
  it("shows the formula read-only, with the server's reason once it arrives", async () => {
    const { container } = setup();

    expect(screen.getByLabelText("Frozen scoring formula").textContent).toBe(FROZEN_FORMULA);
    expect(await screen.findByText("server says: frozen")).toBeTruthy();
    expect(container.querySelector("textarea")).toBeNull();
    expect(screen.queryByText("Reset to default")).toBeNull();
  });

  it("keeps the built-in formula when the server cannot be reached", async () => {
    api.getFrozenFormula.mockRejectedValue(new Error("offline"));
    setup();
    await settled();

    expect(screen.getByLabelText("Frozen scoring formula").textContent).toBe(FROZEN_FORMULA);
    expect(screen.queryByText("server says: frozen")).toBeNull();
  });

  it("never sends a formula when scoring, and never validates one", async () => {
    const { onCycleChange } = setup();
    await settled();

    fireEvent.click(screen.getByText("Run scoring"));
    await waitFor(() => expect(onCycleChange).toHaveBeenCalled());

    // The scan covers the two Rulebook indices unless told otherwise — never
    // the holdings alone, where no rank exit could ever fire.
    expect(api.runPartB).toHaveBeenCalledWith({
      universe_ids: ["midsmall400", "microcap250"],
      filters: { sectors: [], price_min: null, price_max: null, as_of: TODAY },
    });
    expect(api.runPartB.mock.calls[0][0]).not.toHaveProperty("formula");
    expect(api.validateFormula).not.toHaveBeenCalled();
  });
});

describe("ScannerPanel — running", () => {
  it("locks the inputs and explains the wait while scoring", async () => {
    let finish: (c: Cycle) => void = () => undefined;
    api.runPartB.mockReturnValue(new Promise<Cycle>((r) => { finish = r; }));
    const { onCycleChange } = setup();
    await settled();

    fireEvent.click(screen.getByText("Run scoring"));

    expect(await screen.findByText("Scoring…")).toBeTruthy();
    expect(screen.getByText(/Scoring 2 universe\(s\)/)).toBeTruthy();
    expect((screen.getByText("Scoring…") as HTMLButtonElement).disabled).toBe(true);

    finish(cycle({ output: OUTPUT }));
    await waitFor(() => expect(onCycleChange).toHaveBeenCalled());
    expect(screen.queryByText("Scoring…")).toBeNull();
  });

  it("shows the server's message when the run is refused", async () => {
    api.runPartB.mockRejectedValue(new ApiRequestError(409, "formula is frozen" as never));
    const { onCycleChange } = setup();
    await settled();

    fireEvent.click(screen.getByText("Run scoring"));

    expect(await screen.findByText("formula is frozen")).toBeTruthy();
    expect(onCycleChange).not.toHaveBeenCalled();
  });

  it("shows any other failure as text", async () => {
    api.runPartB.mockRejectedValue("network down");
    setup();
    await settled();

    fireEvent.click(screen.getByText("Run scoring"));

    expect(await screen.findByText("network down")).toBeTruthy();
  });

  it("reports a step that failed on the server", async () => {
    setup(cycle({ status: "failed", error: "RuntimeError: price feed down" }));
    await settled();

    expect(screen.getByText("Failed: RuntimeError: price feed down")).toBeTruthy();
  });
});

describe("ScannerPanel — results", () => {
  it("lists the ranking, dashes for missing values, and the summary", async () => {
    const { container } = setup(cycle({ output: OUTPUT, status: "done" }));
    await settled();

    expect(screen.getByText(
      "NIFTY MIDSMALLCAP 400 + Current Holdings · as of 2026-11-13 · 1/2 eligible"))
      .toBeTruthy();
    const rows = container.querySelectorAll("tbody tr");
    expect(rows).toHaveLength(2);
    expect(rows[0].className).toBe("");
    expect(rows[0].textContent).toBe("1AAAAuto120.512.34.2");
    expect(rows[1].className).toBe("row-ineligible");
    expect(rows[1].textContent).toBe("—BBBBank———illiquid, listed<12m");
    expect(screen.getByText("Re-run")).toBeTruthy();
  });

  it("starts from the universes and filters the last run used", async () => {
    setup(cycle({ output: OUTPUT, status: "done" }));
    await settled();

    expect(api.getUniverseSectors).toHaveBeenCalledWith(["midsmall400", "holdings"]);
    expect((screen.getByLabelText("As-of date") as HTMLInputElement).value).toBe("2026-11-13");
    expect(await screen.findByText("2 selected")).toBeTruthy();
  });

  it("advances to the portfolio audit", async () => {
    const { onCycleChange } = setup(cycle({ output: OUTPUT, status: "done" }));
    await settled();

    fireEvent.click(screen.getByText("Acknowledge & proceed to Portfolio Audit"));

    await waitFor(() => expect(onCycleChange).toHaveBeenCalled());
    expect(api.advancePartB).toHaveBeenCalledTimes(1);
    expect(onCycleChange.mock.calls[0][0].current_step).toBe("part_c_exit_flags");
  });

  it("shows why an advance was refused", async () => {
    api.advancePartB.mockRejectedValue(new ApiRequestError(409, "run the step first" as never));
    setup(cycle({ output: OUTPUT, status: "done" }));
    await settled();

    fireEvent.click(screen.getByText("Acknowledge & proceed to Portfolio Audit"));

    expect(await screen.findByText("run the step first")).toBeTruthy();
  });

  it("shows any other advance failure as text", async () => {
    api.advancePartB.mockRejectedValue("boom");
    setup(cycle({ output: OUTPUT, status: "done" }));
    await settled();

    fireEvent.click(screen.getByText("Acknowledge & proceed to Portfolio Audit"));

    expect(await screen.findByText("boom")).toBeTruthy();
  });

  it("hides stale results the moment an input changes", async () => {
    setup(cycle({ output: OUTPUT, status: "done" }));
    await settled();

    fireEvent.change(screen.getByLabelText("Min price"), { target: { value: "50" } });

    expect(screen.getByText("Inputs changed — re-run to refresh the results.")).toBeTruthy();
    expect(screen.queryByText(/eligible$/)).toBeNull();
  });
});

describe("ScannerPanel — filters", () => {
  it("sends the price range and date that were typed, blanks as null", async () => {
    setup();
    await settled();

    fireEvent.change(screen.getByLabelText("Min price"), { target: { value: "50" } });
    fireEvent.change(screen.getByLabelText("Max price"), { target: { value: "900" } });
    fireEvent.change(screen.getByLabelText("Min price"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("As-of date"), { target: { value: "2026-10-30" } });
    fireEvent.click(screen.getByText("Run scoring"));

    await waitFor(() => expect(api.runPartB).toHaveBeenCalled());
    expect(api.runPartB.mock.calls[0][0].filters).toEqual(
      { sectors: [], price_min: null, price_max: 900, as_of: "2026-10-30" });
  });

  it("sends no date when the date is cleared", async () => {
    setup();
    await settled();

    fireEvent.change(screen.getByLabelText("As-of date"), { target: { value: "" } });
    fireEvent.click(screen.getByText("Run scoring"));

    await waitFor(() => expect(api.runPartB).toHaveBeenCalled());
    expect(api.runPartB.mock.calls[0][0].filters.as_of).toBeNull();
  });

  it("refetches sectors and clears the sector filter when the universe changes", async () => {
    setup();
    await screen.findByLabelText("Current Holdings");
    await waitFor(() =>
      expect(api.getUniverseSectors).toHaveBeenCalledWith(["midsmall400", "microcap250"]));

    fireEvent.click(screen.getByLabelText("Auto"));               // pick a sector first
    fireEvent.click(screen.getByLabelText("Current Holdings"));   // then widen the universe

    const widened = ["midsmall400", "microcap250", "holdings"];
    await waitFor(() => expect(api.getUniverseSectors).toHaveBeenCalledWith(widened));
    fireEvent.click(screen.getByText("Run scoring"));
    await waitFor(() => expect(api.runPartB).toHaveBeenCalled());
    expect(api.runPartB.mock.calls[0][0]).toMatchObject(
      { universe_ids: widened, filters: { sectors: [] } });
  });

  it("keeps a chosen sector in the request", async () => {
    setup();
    await screen.findByLabelText("Bank");

    fireEvent.click(screen.getByLabelText("Bank"));
    fireEvent.click(screen.getByText("Run scoring"));

    await waitFor(() => expect(api.runPartB).toHaveBeenCalled());
    expect(api.runPartB.mock.calls[0][0].filters.sectors).toEqual(["Bank"]);
  });

  it("cannot run with no universe selected, and asks for no sectors", async () => {
    setup();
    await screen.findByLabelText("NIFTY MICROCAP 250");
    await waitFor(() => expect(api.getUniverseSectors).toHaveBeenCalled());

    fireEvent.click(screen.getByLabelText("NIFTY MIDSMALLCAP 400"));   // deselect both
    await waitFor(() =>
      expect(api.getUniverseSectors).toHaveBeenLastCalledWith(["microcap250"]));
    api.getUniverseSectors.mockClear();
    fireEvent.click(screen.getByLabelText("NIFTY MICROCAP 250"));

    expect((screen.getByText("Run scoring") as HTMLButtonElement).disabled).toBe(true);
    await waitFor(() => expect(screen.queryByLabelText("Auto")).toBeNull());
    expect(api.getUniverseSectors).not.toHaveBeenCalled();
  });

  it("falls back to no sectors when they cannot be loaded", async () => {
    api.getUniverseSectors.mockRejectedValue(new Error("offline"));
    setup();
    await settled();

    await waitFor(() => expect(api.getUniverseSectors).toHaveBeenCalled());
    expect(screen.queryByLabelText("Auto")).toBeNull();
    expect(screen.getByText("All sectors")).toBeTruthy();
  });
});

describe("ScannerPanel — after the step is advanced", () => {
  it("locks everything and offers to reopen the step", async () => {
    setup(cycle({ output: OUTPUT, status: "done", step: "part_c_exit_flags" }));
    await settled();

    expect(screen.getByText("Advanced — see the next step below.")).toBeTruthy();
    expect(screen.getByText("reopen:part_b_scanner")).toBeTruthy();
    expect((screen.getByText("Re-run") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("Min price") as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByText("Acknowledge & proceed to Portfolio Audit")).toBeNull();
  });
});
