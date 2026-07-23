"""Jerald Score — a scoring formula console (safe expression evaluator).

The formula vocabulary is modeled on the Sigma Scanner, but computed on correct
lookback windows and split/dividend-adjusted data (see the window note below).
Supported vocabulary:
  Terms:   {1 Month, 3 Month, 6 Month, 9 Month, 1 Year} × {Performance, Volatility}
  Ops:     + - * / (also ÷ and x/× as aliases)
  Groups:  ( )
  Weights: N%  (e.g. 60%  ->  0.60)

Definitions:
  Performance(k) = P_t / P_{t-k} - 1              (simple trailing return)
  Volatility(k)  = std-dev of daily returns over the last k   (sample, ddof=1)
  Window = a fixed offset of TRADING candles (rows), ~21 per month — the
  standard quant convention: 1M=21, 3M=63, 6M=126, 9M=189, 1Y=252 candles.

  We deliberately use 126 (not 180) candles for "6 Month". A candle is one
  trading day, and ~21 trade per month, so 126 rows ≈ a true 6 months. Sigma
  uses 180 candles ("6*30"), which is really ~8.5 months mislabeled as 6 —
  matching it would import that convention error, so we don't. Always look back
  a fixed number of ROWS, never a calendar date (a holiday/weekend on the target
  date returns null and breaks the vector math).

Safety: the formula is parsed to an AST; only numeric literals, the whitelisted
term names, unary +/-, and + - * / are permitted. Anything else (calls,
attributes, names, imports) is rejected. Evaluation walks the AST directly — no
eval/exec — so no arbitrary code can run.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Fixed trading-candle offset: candles = months × 21 (one rule, any period —
# 4 Month would be 84, 7 Month 147, etc. — nothing hardcoded per period).
TRADING_CANDLES_PER_MONTH = 21

# Period label -> (short code, months).
_PERIODS = {
    "1 Month": ("1m", 1), "3 Month": ("3m", 3), "6 Month": ("6m", 6),
    "9 Month": ("9m", 9), "1 Year": ("12m", 12),
}
# token code -> candle lookback  ->  {1m:21, 3m:63, 6m:126, 9m:189, 12m:252}
CODE_DAYS = {code: months * TRADING_CANDLES_PER_MONTH for code, months in _PERIODS.values()}

# token (safe identifier) -> (kind, code)
TERMS: dict[str, tuple[str, str]] = {}
_PHRASE_TO_TOKEN: dict[str, str] = {}
for _label, (_code, _months) in _PERIODS.items():
    for _kind, _suffix in (("perf", "Performance"), ("vol", "Volatility")):
        _tok = f"{_kind}_{_code}"
        TERMS[_tok] = (_kind, _code)
        _PHRASE_TO_TOKEN[f"{_label} {_suffix}"] = _tok

DEFAULT_FORMULA = (
    "((60% * 6 Month Performance) + (30% * 3 Month Performance) "
    "+ (10% * 1 Month Performance)) / (3 Month Volatility + 1 Month Volatility)"
)


class FormulaError(ValueError):
    """Raised for an invalid or unsafe scoring formula."""


@dataclass
class CompiledFormula:
    tree: ast.Expression
    used_terms: set[str]
    text: str


def _preprocess(text: str) -> str:
    """Turn the human formula into a plain arithmetic expression over term tokens."""
    s = text.strip()
    if not s:
        raise FormulaError("Formula is empty.")
    # Replace term phrases (longest first, flexible whitespace, case-insensitive).
    for phrase in sorted(_PHRASE_TO_TOKEN, key=len, reverse=True):
        pattern = re.compile(re.escape(phrase).replace(r"\ ", r"\s+"), re.IGNORECASE)
        s = pattern.sub(_PHRASE_TO_TOKEN[phrase], s)
    # Operator aliases: ÷ -> /, × -> *, standalone x/X -> *.
    s = s.replace("÷", "/").replace("×", "*")
    s = re.sub(r"(?<![A-Za-z0-9_])[xX](?![A-Za-z0-9_])", "*", s)
    # Percentages: 60% -> (60/100).
    s = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", s)
    return s


_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd,
    ast.Constant, ast.Name, ast.Load,
)


def compile_formula(text: str) -> CompiledFormula:
    """Parse + validate a formula. Raises FormulaError on any problem."""
    expr = _preprocess(text)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"Could not parse formula: {exc.msg}") from exc

    used: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise FormulaError(f"Disallowed expression element: {type(node).__name__}.")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise FormulaError("Only numbers and the listed terms are allowed.")
        if isinstance(node, ast.Name):
            if node.id not in TERMS:
                raise FormulaError(f"Unknown term: '{node.id}'.")
            used.add(node.id)
    if not used:
        raise FormulaError("Formula must reference at least one Performance/Volatility term.")
    return CompiledFormula(tree=tree, used_terms=used, text=text)


def _eval(node: ast.AST, values: dict[str, float]) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body, values)
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, values)
        return +v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, values), _eval(node.right, values)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return float("nan") if right == 0 else left / right
    raise FormulaError("Unsupported expression.")  # pragma: no cover (guarded by compile)


def term_value(close: pd.Series, token: str) -> float:
    """Compute a single term (Performance/Volatility) as-of the end of `close`."""
    kind, code = TERMS[token]
    days = CODE_DAYS[code]
    if kind == "perf":
        if len(close) <= days:
            return float("nan")
        return float(close.iloc[-1] / close.iloc[-1 - days] - 1.0)
    rets = close.pct_change().dropna()
    if len(rets) < days:
        return float("nan")
    return float(rets.iloc[-days:].std(ddof=1))


def score(close: pd.Series, compiled: CompiledFormula) -> float:
    """Evaluate a compiled formula for one stock's close series. NaN if any term is undefined."""
    values = {t: term_value(close, t) for t in compiled.used_terms}
    if any(np.isnan(v) for v in values.values()):
        return float("nan")
    return _eval(compiled.tree, values)
