import { useEffect, useState } from "react";
import { api, ApiRequestError } from "../../api/client";
import type { BacktestMetrics, BacktestResult } from "../../api/types";
import { LineChart } from "../../components/LineChart";

// Validated categorical slots 1 & 2 for a dark surface (see the palette check:
// ΔE 26.8 under protanopia, well above the separation floor).
const STRATEGY = "#3987e5";
const BENCHMARK = "#d95926";

const inr = (v: number | null | undefined) =>
  v == null ? "—" : `₹${Math.round(v).toLocaleString("en-IN")}`;

type View = "chart" | "weeks" | "trades" | "table";

export function RunResult({ runId }: { runId: string }) {
  const [res, setRes] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>("chart");

  useEffect(() => {
    setRes(null);
    setError(null);
    api.getBacktestResult(runId)
      .then(setRes)
      .catch((e) => setError(e instanceof ApiRequestError ? e.message : String(e)));
  }, [runId]);

  if (error) return <p className="error-box">{error}</p>;
  if (!res) return <p className="muted">Loading result…</p>;

  const m = res.metrics;
  const b = res.benchmark;
  const edge = m.cagr_pct - b.cagr_pct;

  return (
    <div className="hist-detail">
      <div className="hist-head">
        <h4 className="jr-head">
          {String(res.config.universe_id)} · {String(res.config.start)} → {String(res.config.end)}
        </h4>
        <span className="muted">
          {String(res.config.filter_label)} on {String(res.config.index_label)} ·{" "}
          {String(res.config.universe_size)} names
        </span>
      </div>

      {/* The headline: did the strategy beat simply holding the index? */}
      <div className="port-stats">
        <Stat label="CAGR" value={`${m.cagr_pct}%`}
              note={`index ${b.cagr_pct}%`} good={edge >= 0} />
        <Stat label="Sharpe" value={String(m.sharpe)} note={`index ${b.sharpe}`}
              good={m.sharpe >= b.sharpe} />
        <Stat label="Max drawdown" value={`${m.max_drawdown_pct}%`}
              note={`index ${b.max_drawdown_pct}%`} good={m.max_drawdown_pct >= b.max_drawdown_pct} />
        <Stat label="Final equity" value={inr(m.end_equity)}
              note={`from ${inr(m.start_equity)}`} good={m.end_equity >= m.start_equity} />
      </div>

      <div className="bt-tabs">
        {(["chart", "weeks", "trades", "table"] as View[]).map((v) => (
          <button key={v} className={`ghost${view === v ? " bt-tab-active" : ""}`}
                  onClick={() => setView(v)}>
            {v === "table" ? "Data" : v[0].toUpperCase() + v.slice(1)}
          </button>
        ))}
      </div>

      {view === "chart" && (
        <LineChart
          ariaLabel="Strategy equity versus buy-and-hold index"
          series={[
            { name: "Strategy", color: STRATEGY,
              points: res.equity_curve.map((p) => ({ x: p.date, y: p.equity })) },
            { name: "Index", color: BENCHMARK,
              points: res.benchmark_curve.map((p) => ({ x: p.date, y: p.equity })) },
          ]}
        />
      )}

      {view === "chart" && (
        <div className="cap-grid">
          <MetricTable title="Strategy" m={m} extra={{
            Turnover: `${res.turnover}x/yr`,
            "Closed trades": String(res.trade_stats.closed_trades ?? 0),
            "Trade win rate": `${res.trade_stats.win_rate_pct ?? 0}%`,
          }} />
          <MetricTable title="Buy &amp; hold index" m={b} />
        </div>
      )}

      {view === "weeks" && (
        <div className="scan-table-wrap">
          <table className="scan-table">
            <thead>
              <tr>
                <th>Week</th><th>Regime</th><th className="num">Eligible</th>
                <th>Buys</th><th>Exits</th><th>Stops hit</th>
                <th className="num">Held</th><th className="num">Equity</th>
              </tr>
            </thead>
            <tbody>
              {res.weeks.map((w) => (
                <tr key={w.week_end} className={w.regime === "RED" ? "row-exit" : ""}>
                  <td>{w.week_end}</td>
                  <td>
                    <span className={w.regime === "GREEN" ? "tag-keep" : "tag-exit"}>
                      {w.regime}
                    </span>
                  </td>
                  <td className="num">{w.eligible}</td>
                  <td className="flags">{w.buys.join(", ")}</td>
                  <td className="flags">{w.exits.join(", ")}</td>
                  <td className="flags">{w.stops_hit.join(", ")}</td>
                  <td className="num">{w.held.length}</td>
                  <td className="num">{inr(w.equity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {view === "trades" && (
        <div className="scan-table-wrap">
          <table className="scan-table">
            <thead>
              <tr>
                <th>Date</th><th>Symbol</th><th>Side</th><th className="num">Qty</th>
                <th className="num">Price</th><th className="num">Value</th>
                <th className="num">Charges</th><th>Why</th>
              </tr>
            </thead>
            <tbody>
              {res.trades.map((t, i) => (
                <tr key={i} className={t.side === "sell" ? "row-exit" : ""}>
                  <td>{t.date}</td>
                  <td>{t.symbol}</td>
                  <td>
                    <span className={t.side === "buy" ? "tag-keep" : "tag-exit"}>
                      {t.side.toUpperCase()}
                    </span>
                  </td>
                  <td className="num">{t.qty}</td>
                  <td className="num">{t.price}</td>
                  <td className="num">{inr(t.value)}</td>
                  <td className="num">{inr(t.charges)}</td>
                  <td className="flags">{t.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* The chart's data as a table — the accessible equivalent of the plot. */}
      {view === "table" && (
        <div className="scan-table-wrap">
          <table className="scan-table">
            <thead>
              <tr>
                <th>Date</th><th className="num">Strategy</th><th className="num">Index</th>
                <th className="num">Cash</th><th className="num">Positions</th><th>Regime</th>
              </tr>
            </thead>
            <tbody>
              {res.equity_curve.map((p, i) => (
                <tr key={p.date}>
                  <td>{p.date}</td>
                  <td className="num">{inr(p.equity)}</td>
                  <td className="num">{inr(res.benchmark_curve[i]?.equity)}</td>
                  <td className="num">{inr(p.cash)}</td>
                  <td className="num">{p.positions ?? "—"}</td>
                  <td>{p.regime}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h4 className="jr-head">What this result does not account for</h4>
      <ul className="actions">
        {res.limitations.map((l, i) => <li key={i} className="muted">{l}</li>)}
      </ul>
    </div>
  );
}

function Stat({ label, value, note, good }:
              { label: string; value: string; note: string; good: boolean }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className={`stat-value ${good ? "v-pass" : "v-strike"}`}>{value}</span>
      <span className="stat-note">{note}</span>
    </div>
  );
}

function MetricTable({ title, m, extra }:
                     { title: string; m: BacktestMetrics; extra?: Record<string, string> }) {
  const rows: [string, string][] = [
    ["Total return", `${m.total_return_pct}%`],
    ["CAGR", `${m.cagr_pct}%`],
    ["Volatility (ann.)", `${m.volatility_pct}%`],
    ["Sharpe", String(m.sharpe)],
    ["Sortino", String(m.sortino)],
    ["Max drawdown", `${m.max_drawdown_pct}% (${m.max_drawdown_weeks}w)`],
    ["Weeks", String(m.weeks)],
    ["Weeks invested", `${m.exposure_pct}%`],
    ["Positive weeks (all)", `${m.win_rate_pct}%`],
    ["Positive weeks (invested)", `${m.win_rate_invested_pct || m.win_rate_pct}%`],
    ...Object.entries(extra ?? {}) as [string, string][],
  ];
  return (
    <div>
      <h4 dangerouslySetInnerHTML={{ __html: title }} />
      <table className="scan-table">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}><td>{k}</td><td className="num">{v}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
