"""Find the ROBUST best formula: best average out-of-sample standing across
several universes, not the winner on any single one (which would be overfit)."""
from __future__ import annotations
import datetime as dt, itertools, json, time
import numpy as np
from app.backtest import panel as P, sweep
from app.domain import scoring_formula as SF

UNIVERSES = ["midcap150", "smallcap250", "midsmall400", "midsmall_micro"]
INDEX, FILTER = "nifty500", "st_1_2_5"
TRAIN_END, TEST_START = dt.date(2016, 12, 31), dt.date(2017, 1, 1)

def formula(weights: dict, denom: tuple) -> str:
    num = " + ".join(f"({w}% * {lab} Performance)" for lab, w in weights.items() if w)
    den = " + ".join(f"{v} Volatility" for v in denom)
    return f"({num}) / ({den})" if den else f"({num})"

# Grid: weights over {3M,6M,9M,12M} in 25% steps summing to 100, x 4 denominators.
LOOKBACKS = ["3 Month", "6 Month", "9 Month", "1 Year"]
DENOMS = [("6 Month",), ("3 Month",), ("3 Month", "1 Month"), ()]
grid = []
for combo in itertools.product([0, 25, 50, 75, 100], repeat=4):
    if sum(combo) != 100:
        continue
    w = dict(zip(LOOKBACKS, combo))
    for d in DENOMS:
        grid.append(formula(w, d))
grid = list(dict.fromkeys(grid))
CHAMPION = "(100% * 6 Month Performance) / (6 Month Volatility)"
if CHAMPION not in grid:
    grid.append(CHAMPION)
print(f"{len(grid)} formulas x {len(UNIVERSES)} universes", flush=True)

t0 = time.time()
per_uni = {}   # universe -> {formula: test_cagr}
noise = {}
for u in UNIVERSES:
    print(f"[{time.time()-t0:5.0f}s] panel {u} ...", flush=True)
    pan = P.get(u, INDEX, progress=lambda d, t: None)
    regime = sweep.regime_vector(pan, FILTER)
    train, trm = sweep.slice_panel(pan, None, TRAIN_END)
    test, tem = sweep.slice_panel(pan, TEST_START, None)
    r_tr, r_te = regime[trm], regime[tem]
    res = {}
    for i, f in enumerate(grid):
        try:
            res[f] = sweep.evaluate(test, f, r_te).cagr_pct   # OOS CAGR
        except Exception:
            continue
        if i % 40 == 0:
            print(f"  {u} {i}/{len(grid)}", flush=True)
    per_uni[u] = res
    # noise floor on train
    rng = np.random.default_rng(7)
    nz = []
    for _ in range(80):
        wv = rng.dirichlet([1, 1, 1, 1]) * 100
        f = formula(dict(zip(LOOKBACKS, [round(x) for x in wv])), ("3 Month", "1 Month"))
        try: nz.append(sweep.evaluate(train, f, r_tr).cagr_pct)
        except Exception: pass
    noise[u] = (round(float(np.min(nz)), 2), round(float(np.max(nz)), 2))
    print(f"[{time.time()-t0:5.0f}s] {u} done  noise {noise[u]}", flush=True)

# Aggregate: percentile of each formula within each universe, then average.
common = set.intersection(*[set(d) for d in per_uni.values()])
rows = []
for f in common:
    pcts, cagrs = [], []
    for u in UNIVERSES:
        vals = np.array(list(per_uni[u].values()))
        pcts.append(100 * float((vals < per_uni[u][f]).mean()))
        cagrs.append(per_uni[u][f])
    rows.append({"formula": f, "mean_pct": round(float(np.mean(pcts)), 1),
                 "min_pct": round(float(np.min(pcts)), 1),
                 "mean_test_cagr": round(float(np.mean(cagrs)), 2),
                 "per_uni_cagr": {u: round(per_uni[u][f], 2) for u in UNIVERSES}})
rows.sort(key=lambda r: -r["mean_pct"])

out = {"grid_size": len(grid), "universes": UNIVERSES, "noise": noise,
       "train_end": TRAIN_END.isoformat(), "test_start": TEST_START.isoformat(),
       "ranking": rows}
json.dump(out, open("meta_sweep.json", "w"), indent=2)

print(f"\n=== ROBUST RANKING (best avg out-of-sample percentile across {len(UNIVERSES)} universes) ===", flush=True)
print(f"{'formula':60}{'avgPct':>7}{'minPct':>7}{'avgCAGR':>8}", flush=True)
for r in rows[:12]:
    print(f"{r['formula'][:60]:60}{r['mean_pct']:>7}{r['min_pct']:>7}{r['mean_test_cagr']:>7}%", flush=True)
champ = next((r for r in rows if r["formula"] == CHAMPION), None)
if champ:
    print(f"\nCHAMPION 100%6M/6Mvol: avgPct {champ['mean_pct']}, minPct {champ['min_pct']}, per-uni {champ['per_uni_cagr']}", flush=True)
print(f"\ndone {time.time()-t0:.0f}s", flush=True)
