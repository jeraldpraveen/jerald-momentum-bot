"""Best Jerald Score for the ACTUAL trading universe: MIDSMALLCAP400 + MICROCAP250.

Walk-forward: tune on <=2016, judge on >2017. Wider grid than before — includes
3/6/9/12-month lookbacks and four volatility denominators. Reports the winner
read against the noise floor and its out-of-sample percentile, and every honest
diagnostic, exactly like the UI sweep.
"""
from __future__ import annotations
import datetime as dt, itertools, json, time
import numpy as np
from app.backtest import panel as P, sweep

UNIVERSE, INDEX, FILTER = "midsmall_micro", "nifty500", "st_1_2_5"
TRAIN_END, TEST_START = dt.date(2016, 12, 31), dt.date(2017, 1, 1)

def formula(w: dict, denom: tuple) -> str:
    num = " + ".join(f"({v}% * {lab} Performance)" for lab, v in w.items() if v)
    den = " + ".join(f"{d} Volatility" for d in denom)
    return f"({num}) / ({den})" if den else f"({num})"

LB = ["3 Month", "6 Month", "9 Month", "1 Year"]
DENOMS = [("6 Month",), ("3 Month",), ("3 Month", "1 Month"), ("6 Month", "3 Month"), ()]
grid = []
for combo in itertools.product([0, 25, 50, 75, 100], repeat=4):
    if sum(combo) != 100: continue
    for d in DENOMS:
        grid.append(formula(dict(zip(LB, combo)), d))
grid = list(dict.fromkeys(grid))
CHAMP = "(100% * 6 Month Performance) / (6 Month Volatility)"
if CHAMP not in grid: grid.append(CHAMP)
BASE = "((60% * 6 Month Performance) + (30% * 3 Month Performance) + (10% * 1 Month Performance)) / (3 Month Volatility + 1 Month Volatility)"

t0 = time.time()
print(f"{len(grid)} formulas on {UNIVERSE}", flush=True)
pan = P.get(UNIVERSE, INDEX, progress=lambda d,t: print(f"  panel {d}/{t}", flush=True))
regime = sweep.regime_vector(pan, FILTER)
train, trm = sweep.slice_panel(pan, None, TRAIN_END)
test, tem = sweep.slice_panel(pan, TEST_START, None)
r_tr, r_te = regime[trm], regime[tem]
print(f"[{time.time()-t0:.0f}s] train {len(train.weeks)}w, test {len(test.weeks)}w", flush=True)

rows = []
for i, f in enumerate(grid):
    try:
        tr = sweep.evaluate(train, f, r_tr); te = sweep.evaluate(test, f, r_te)
    except Exception: continue
    rows.append({"formula": f, "train_cagr": tr.cagr_pct, "test_cagr": te.cagr_pct,
                 "test_sharpe": te.sharpe, "test_maxdd": te.max_drawdown_pct})
    if i % 25 == 0: print(f"  {i}/{len(grid)}", flush=True)

# noise floor (random weightings) on train
rng = np.random.default_rng(11); nz=[]
for _ in range(120):
    wv = rng.dirichlet([1,1,1,1])*100
    try: nz.append(sweep.evaluate(train, formula(dict(zip(LB,[round(x) for x in wv])), ("3 Month","1 Month")), r_tr).cagr_pct)
    except Exception: pass
nz = np.array(nz)

te_all = np.array([r["test_cagr"] for r in rows]); tr_all = np.array([r["train_cagr"] for r in rows])
winner = max(rows, key=lambda r: r["train_cagr"])          # chosen on TRAIN only
base_te = sweep.evaluate(test, BASE, r_te).cagr_pct
champ = next((r for r in rows if r["formula"]==CHAMP), None)

out = {"grid": len(grid), "noise": [float(nz.min()), float(nz.max())],
       "winner": winner, "winner_test_pct": round(100*float((te_all<winner['test_cagr']).mean()),1),
       "baseline_test_cagr": round(base_te,2), "median_test": round(float(np.median(te_all)),2),
       "pct_beating_base": round(100*float((te_all>base_te).mean()),1),
       "train_test_corr": round(float(np.corrcoef(tr_all,te_all)[0,1]),3),
       "champion_100_6M": champ, "top": sorted(rows, key=lambda r:-r["train_cagr"])[:10]}
json.dump(out, open("focus_sweep.json","w"), indent=2)

print(f"\n=== MIDSMALLCAP400 + MICROCAP250 ===", flush=True)
print(f"noise floor (random): {nz.min():.1f}%..{nz.max():.1f}%", flush=True)
print(f"baseline (old Jerald): test {base_te:.2f}%   | {out['pct_beating_base']}% of formulas beat it", flush=True)
print(f"train->test corr: {out['train_test_corr']}   median test {out['median_test']}%", flush=True)
print(f"\nWINNER (picked on train):  {winner['formula']}", flush=True)
print(f"  train {winner['train_cagr']:.2f}%  -> TEST {winner['test_cagr']:.2f}%  Sharpe {winner['test_sharpe']}  ({out['winner_test_pct']}th pct)", flush=True)
if champ: print(f"100% 6M / 6M vol:  train {champ['train_cagr']:.2f}% -> TEST {champ['test_cagr']:.2f}%  Sharpe {champ['test_sharpe']}", flush=True)
print(f"\nTOP 10 BY TRAIN (with their TEST results):", flush=True)
for r in sorted(rows, key=lambda r:-r['train_cagr'])[:10]:
    print(f"  tr {r['train_cagr']:6.2f} te {r['test_cagr']:6.2f} sh {r['test_sharpe']:5.2f}  {r['formula'][:62]}", flush=True)
print(f"\ndone {time.time()-t0:.0f}s", flush=True)
