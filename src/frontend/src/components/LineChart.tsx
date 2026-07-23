import { useMemo, useRef, useState } from "react";

export interface Point {
  x: string;          // ISO date
  y: number;
}

export interface Series {
  name: string;
  color: string;
  points: Point[];
}

interface Props {
  series: Series[];
  height?: number;
  yFormat?: (v: number) => string;
  ariaLabel?: string;
}

const W = 900;                       // viewBox width; the SVG scales to its box
const PAD = { top: 16, right: 76, bottom: 26, left: 62 };

const fmtInr = (v: number) =>
  v >= 1e7 ? `₹${(v / 1e7).toFixed(2)}Cr`
    : v >= 1e5 ? `₹${(v / 1e5).toFixed(1)}L`
      : `₹${Math.round(v).toLocaleString("en-IN")}`;

// Equity curve: strategy vs benchmark over time. Two series, so a legend is
// always present and each line is also labelled at its end — identity is never
// carried by colour alone. Colours are the validated categorical slots 1 and 2
// (blue/orange), which stay separable under protanopia and deuteranopia.
export function LineChart({ series, height = 300, yFormat = fmtInr, ariaLabel }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverX, setHoverX] = useState<number | null>(null);

  const model = useMemo(() => {
    const all = series.flatMap((s) => s.points);
    if (all.length === 0) return null;

    const xs = all.map((p) => new Date(p.x).getTime());
    const ys = all.map((p) => p.y);
    const x0 = Math.min(...xs);
    const x1 = Math.max(...xs);
    const yMin = Math.min(...ys);
    const yMax = Math.max(...ys);
    // Pad the y-range by 5% so lines never graze the frame.
    const span = yMax - yMin || Math.abs(yMax) || 1;
    const lo = yMin - span * 0.05;
    const hi = yMax + span * 0.05;

    const plotW = W - PAD.left - PAD.right;
    const plotH = height - PAD.top - PAD.bottom;
    const sx = (t: number) => PAD.left + (x1 === x0 ? plotW / 2 : ((t - x0) / (x1 - x0)) * plotW);
    const sy = (v: number) => PAD.top + plotH - ((v - lo) / (hi - lo)) * plotH;

    const ticks = Array.from({ length: 5 }, (_, i) => lo + ((hi - lo) * i) / 4);
    const xTicks = Array.from({ length: 5 }, (_, i) => x0 + ((x1 - x0) * i) / 4);

    const paths = series.map((s) => ({
      ...s,
      d: s.points
        .map((p, i) => `${i ? "L" : "M"}${sx(new Date(p.x).getTime()).toFixed(1)},${sy(p.y).toFixed(1)}`)
        .join(" "),
      last: s.points.length ? s.points[s.points.length - 1] : null,
    }));

    return { sx, sy, ticks, xTicks, paths, plotW, plotH, x0, x1 };
  }, [series, height]);

  if (!model) return <p className="muted">No data to plot.</p>;

  // Nearest point per series to the hovered x, for the crosshair tooltip.
  const hovered = hoverX == null ? null : series.map((s) => {
    let best: Point | null = null;
    let bestDist = Infinity;
    for (const p of s.points) {
      const d = Math.abs(model.sx(new Date(p.x).getTime()) - hoverX);
      if (d < bestDist) { bestDist = d; best = p; }
    }
    return { name: s.name, color: s.color, point: best };
  });

  const anchor = hovered?.find((h) => h.point)?.point ?? null;
  const anchorX = anchor ? model.sx(new Date(anchor.x).getTime()) : null;

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const px = ((e.clientX - rect.left) / rect.width) * W;
    setHoverX(px < PAD.left || px > W - PAD.right ? null : px);
  }

  return (
    <figure className="chart">
      <div className="chart-legend">
        {series.map((s) => (
          <span key={s.name} className="chart-legend-item">
            <span className="chart-swatch" style={{ background: s.color }} aria-hidden="true" />
            {s.name}
          </span>
        ))}
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${height}`}
        className="chart-svg"
        role="img"
        aria-label={ariaLabel ?? "Equity curve"}
        onMouseMove={onMove}
        onMouseLeave={() => setHoverX(null)}
      >
        {/* Recessive grid — present enough to read a value, quiet enough to ignore. */}
        {model.ticks.map((t, i) => (
          <g key={i}>
            <line x1={PAD.left} x2={W - PAD.right} y1={model.sy(t)} y2={model.sy(t)}
                  className="chart-grid" />
            <text x={PAD.left - 8} y={model.sy(t) + 4} className="chart-tick" textAnchor="end">
              {yFormat(t)}
            </text>
          </g>
        ))}

        {model.xTicks.map((t, i) => (
          <text key={i} x={model.sx(t)} y={height - 8} className="chart-tick" textAnchor="middle">
            {new Date(t).toISOString().slice(0, 7)}
          </text>
        ))}

        {anchorX != null && (
          <line x1={anchorX} x2={anchorX} y1={PAD.top} y2={height - PAD.bottom}
                className="chart-crosshair" />
        )}

        {model.paths.map((s) => (
          <path key={s.name} d={s.d} fill="none" stroke={s.color} strokeWidth={2}
                strokeLinejoin="round" strokeLinecap="round" />
        ))}

        {/* Direct labels at each line's end, so the legend is not the only key. */}
        {model.paths.map((s) => s.last && (
          <text key={`${s.name}-label`} x={W - PAD.right + 8}
                y={model.sy(s.last.y) + 4} className="chart-endlabel" fill={s.color}>
            {s.name}
          </text>
        ))}

        {hovered?.map((h) => h.point && (
          <circle key={h.name} cx={model.sx(new Date(h.point.x).getTime())}
                  cy={model.sy(h.point.y)} r={5} fill={h.color}
                  className="chart-dot" />
        ))}
      </svg>

      {hovered && anchor && (
        <div className="chart-tooltip"
             style={{ left: `${((anchorX ?? 0) / W) * 100}%` }}>
          <div className="chart-tooltip-date">{anchor.x}</div>
          {hovered.map((h) => h.point && (
            <div key={h.name} className="chart-tooltip-row">
              <span className="chart-swatch" style={{ background: h.color }} aria-hidden="true" />
              {h.name}<b>{yFormat(h.point.y)}</b>
            </div>
          ))}
        </div>
      )}
    </figure>
  );
}
