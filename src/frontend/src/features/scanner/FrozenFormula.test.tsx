import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DEFAULT_REASON, FROZEN_FORMULA, FrozenFormula } from "./FrozenFormula";

describe("FrozenFormula", () => {
  it("shows the built-in frozen formula and why it is frozen", () => {
    render(<FrozenFormula />);

    expect(screen.getByLabelText("Frozen scoring formula").textContent).toBe(FROZEN_FORMULA);
    expect(screen.getByText(DEFAULT_REASON)).toBeTruthy();
    expect(screen.getByText("Frozen · Spec v2.3")).toBeTruthy();
  });

  it("is the 60/30/10 over 3M+1M volatility score the spec froze", () => {
    expect(FROZEN_FORMULA).toBe(
      "((60% * 6 Month Performance) + (30% * 3 Month Performance) + (10% * 1 Month Performance)) " +
      "/ (3 Month Volatility + 1 Month Volatility)");
  });

  it("prefers the formula and reason the server sends", () => {
    render(<FrozenFormula formula="6 Month Performance" reason="because the sweeps said so" />);

    expect(screen.getByLabelText("Frozen scoring formula").textContent).toBe("6 Month Performance");
    expect(screen.getByText("because the sweeps said so")).toBeTruthy();
    expect(screen.queryByText(DEFAULT_REASON)).toBeNull();
  });

  it("falls back to the built-in text when the server sends blanks", () => {
    render(<FrozenFormula formula="" reason="" />);

    expect(screen.getByLabelText("Frozen scoring formula").textContent).toBe(FROZEN_FORMULA);
    expect(screen.getByText(DEFAULT_REASON)).toBeTruthy();
  });

  it("offers nothing to type into or reset", () => {
    const { container } = render(<FrozenFormula />);

    expect(container.querySelector("textarea, input, button")).toBeNull();
  });
});
