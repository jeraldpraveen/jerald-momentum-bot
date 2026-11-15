"""Scoring-formula console: parsing, evaluation, safety."""

import numpy as np
import pandas as pd
import pytest

from app.domain import scoring_formula as sf
from app.domain.scoring_formula import (
    DEFAULT_FORMULA,
    CompiledFormula,
    FormulaError,
    compile_formula,
    score,
)


def series(vals):
    return pd.Series(np.asarray(vals, dtype=float),
                     index=pd.date_range("2023-01-01", periods=len(vals), freq="D"))


def test_default_formula_compiles_with_expected_terms():
    c = compile_formula(DEFAULT_FORMULA)
    assert c.used_terms == {"perf_6m", "perf_3m", "perf_1m", "vol_3m", "vol_1m"}


def test_operator_aliases_and_percent():
    # ÷ and x aliases, and N% -> N/100.
    c = compile_formula("50% x 6 Month Performance ÷ 1 Month Volatility")
    assert c.used_terms == {"perf_6m", "vol_1m"}


def test_evaluates_to_manual_value():
    s = series(np.linspace(100, 260, 300))
    c = compile_formula("6 Month Performance / 1 Month Volatility")
    close = s
    perf6 = close.iloc[-1] / close.iloc[-1 - 126] - 1        # 6 * 21 candles
    vol1 = close.pct_change().dropna().iloc[-21:].std(ddof=1)  # 1 * 21 candles
    assert score(s, c) == pytest.approx(perf6 / vol1)


def test_weights_matching_default_are_reproducible():
    s = series(np.linspace(100, 300, 300))
    manual = compile_formula(
        "(0.6*6 Month Performance + 0.3*3 Month Performance + 0.1*1 Month Performance)"
        "/(3 Month Volatility + 1 Month Volatility)")
    pct = compile_formula(DEFAULT_FORMULA)
    assert score(s, manual) == pytest.approx(score(s, pct))


def test_nan_when_insufficient_history():
    s = series(np.linspace(100, 120, 40))          # < 126 days for 6M
    assert np.isnan(score(s, compile_formula(DEFAULT_FORMULA)))


def test_unknown_term_rejected():
    with pytest.raises(FormulaError):
        compile_formula("6 Month Momentum / 1 Month Volatility")


def test_empty_formula_rejected():
    with pytest.raises(FormulaError):
        compile_formula("   ")


@pytest.mark.parametrize("bad", [
    "__import__('os').system('echo hi')",
    "6 Month Performance.__class__",
    "open('x')",
    "perf_6m if True else 0",
])
def test_unsafe_input_rejected(bad):
    with pytest.raises(FormulaError):
        compile_formula(bad)


def test_division_by_zero_is_nan():
    s = series(np.linspace(100, 200, 300))
    c = CompiledFormula(tree=compile_formula("6 Month Performance - 6 Month Performance").tree,
                        used_terms={"perf_6m"}, text="")
    # numerator forced to 0 via same-term subtraction in denominator position:
    c2 = compile_formula("6 Month Performance / (6 Month Performance - 6 Month Performance)")
    assert np.isnan(score(s, c2))


# --- Spec v2.3: the frozen live formula --------------------------------------

def test_the_frozen_formula_is_the_default_and_compiles():
    assert sf.FROZEN_FORMULA == DEFAULT_FORMULA
    assert compile_formula(sf.FROZEN_FORMULA).used_terms == {
        "perf_6m", "perf_3m", "perf_1m", "vol_3m", "vol_1m"}


@pytest.mark.parametrize("text", [
    "", "  \n ",                                            # blank = use the default
    DEFAULT_FORMULA,
    DEFAULT_FORMULA.upper(),                                # case does not matter
    DEFAULT_FORMULA.replace(" ", "  "),                     # nor spacing
    DEFAULT_FORMULA.replace("*", "x").replace("/", "÷"),    # nor operator aliases
])
def test_respellings_of_the_frozen_formula_still_match(text):
    assert sf.is_frozen(text) is True


@pytest.mark.parametrize("text", [
    DEFAULT_FORMULA.replace("60%", "61%"),                  # a weight
    DEFAULT_FORMULA.replace("6 Month Performance", "9 Month Performance"),   # a term
    DEFAULT_FORMULA.replace(" / (3 Month Volatility + 1 Month Volatility)", ""),  # structure
    "6 Month Performance",
    "not a formula at all",
])
def test_any_real_change_is_not_the_frozen_formula(text):
    assert sf.is_frozen(text) is False


def test_non_numeric_constants_are_rejected():
    with pytest.raises(FormulaError, match="Only numbers"):
        compile_formula("'abc' * 6 Month Performance")


def test_unknown_names_are_rejected():
    with pytest.raises(FormulaError, match="Unknown term: 'foo'"):
        compile_formula("foo * 6 Month Performance")


def test_a_formula_with_no_terms_is_rejected():
    with pytest.raises(FormulaError, match="at least one"):
        compile_formula("1 + 2")


def test_unary_operators_evaluate():
    s = series(np.linspace(100, 200, 300))
    plain = score(s, compile_formula("6 Month Performance"))
    assert score(s, compile_formula("-6 Month Performance")) == pytest.approx(-plain)
    assert score(s, compile_formula("+6 Month Performance")) == pytest.approx(plain)
