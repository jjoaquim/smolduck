// Build a Plotly figure spec from a query result + an encoding config, and emit
// the equivalent Plotly-Express Python for "copy as code". Pure data → no DOM.

export const CHART_TYPES = [
  { id: "bar", label: "Bar", enc: ["x", "y", "color"] },
  { id: "line", label: "Line", enc: ["x", "y", "color"] },
  { id: "scatter", label: "Scatter", enc: ["x", "y", "color"] },
  { id: "histogram", label: "Histogram", enc: ["x", "color"] },
  { id: "box", label: "Box", enc: ["x", "y"] },
  { id: "heatmap", label: "Heatmap", enc: ["x", "y", "z"] },
];

const PX_FN = {
  bar: "bar", line: "line", scatter: "scatter",
  histogram: "histogram", box: "box", heatmap: "density_heatmap",
};

function colIndex(columns, name) {
  return name ? columns.findIndex((c) => c.name === name) : -1;
}

function colValues(columns, rows, name) {
  const i = colIndex(columns, name);
  return i < 0 ? null : rows.map((r) => r[i]);
}

function xyTraces(columns, rows, config, base) {
  const xs = colValues(columns, rows, config.x);
  const ys = colValues(columns, rows, config.y);
  if (!xs || !ys) return [];
  if (config.color) {
    const ci = colIndex(columns, config.color);
    const groups = new Map();
    rows.forEach((r, ri) => {
      const k = r[ci];
      if (!groups.has(k)) groups.set(k, { x: [], y: [] });
      groups.get(k).x.push(xs[ri]);
      groups.get(k).y.push(ys[ri]);
    });
    return [...groups.entries()].map(([name, d]) => ({ ...base, name: String(name), x: d.x, y: d.y }));
  }
  return [{ ...base, x: xs, y: ys }];
}

function histTraces(columns, rows, config) {
  const xs = colValues(columns, rows, config.x);
  if (!xs) return [];
  if (config.color) {
    const ci = colIndex(columns, config.color);
    const groups = new Map();
    rows.forEach((r, ri) => {
      const k = r[ci];
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(xs[ri]);
    });
    return [...groups.entries()].map(([name, x]) => ({ type: "histogram", name: String(name), x, opacity: 0.7 }));
  }
  return [{ type: "histogram", x: xs }];
}

function boxTraces(columns, rows, config) {
  const ys = colValues(columns, rows, config.y);
  if (!ys) return [];
  const xs = config.x ? colValues(columns, rows, config.x) : null;
  return [{ type: "box", y: ys, ...(xs ? { x: xs } : {}), name: config.y }];
}

function heatmapTraces(columns, rows, config) {
  const xs = colValues(columns, rows, config.x);
  const ys = colValues(columns, rows, config.y);
  const zs = colValues(columns, rows, config.z);
  if (!xs || !ys || !zs) return [];
  const ux = [...new Set(xs)];
  const uy = [...new Set(ys)];
  const z = uy.map(() => ux.map(() => null));
  rows.forEach((r, ri) => {
    const xi = ux.indexOf(xs[ri]);
    const yi = uy.indexOf(ys[ri]);
    if (xi >= 0 && yi >= 0) z[yi][xi] = zs[ri];
  });
  return [{ type: "heatmap", x: ux, y: uy, z, colorscale: "Viridis" }];
}

/** Returns a Plotly {data, layout} spec, or null if the encoding is incomplete. */
export function buildFigure(columns, rows, config) {
  if (!columns || !rows || !config || !config.type) return null;
  const t = config.type;
  let data = [];
  if (t === "bar") data = xyTraces(columns, rows, config, { type: "bar" });
  else if (t === "line") data = xyTraces(columns, rows, config, { type: "scatter", mode: "lines+markers" });
  else if (t === "scatter") data = xyTraces(columns, rows, config, { type: "scatter", mode: "markers" });
  else if (t === "histogram") data = histTraces(columns, rows, config);
  else if (t === "box") data = boxTraces(columns, rows, config);
  else if (t === "heatmap") data = heatmapTraces(columns, rows, config);
  if (!data.length) return null;

  const yTitle = t === "histogram" ? "count" : config.y || config.z || "";
  const layout = {
    margin: { t: config.title ? 36 : 20, r: 16, b: 44, l: 60 },
    showlegend: !!config.color,
    xaxis: { title: { text: config.x || "" } },
    yaxis: { title: { text: yTitle } },
  };
  if (config.title) layout.title = { text: config.title };
  if (t === "bar" && config.color) layout.barmode = "group";
  if (t === "histogram" && config.color) layout.barmode = "overlay";
  return { data, layout };
}

/** Emit a runnable Plotly-Express Python cell equivalent to this chart. */
export function codeFor(query, config) {
  const t = (config && config.type) || "bar";
  const fn = PX_FN[t] || "bar";
  const q = (query || "").trim();
  const args = ["df"];
  const push = (k, v) => {
    if (v) args.push(`${k}="${v}"`);
  };
  if (t === "heatmap") {
    push("x", config.x);
    push("y", config.y);
    push("z", config.z);
    args.push('histfunc="avg"');
  } else if (t === "histogram") {
    push("x", config.x);
    push("color", config.color);
  } else if (t === "box") {
    push("x", config.x);
    push("y", config.y);
  } else {
    push("x", config.x);
    push("y", config.y);
    push("color", config.color);
  }
  if (config.title) args.push(`title="${String(config.title).replace(/"/g, '\\"')}"`);
  return `df = sql("""${q}""")\npx.${fn}(${args.join(", ")})`;
}
