import { html } from "htm/preact";
import { useEffect, useRef } from "preact/hooks";

// Render a Plotly figure spec (from the kernel) using the lazily-imported
// plotly.js — CDN in native dev, self-hosted under /vendor in the offline VM.

const DARK_LAYOUT = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { color: "#cdbfa8", family: "Hanken Grotesk, sans-serif", size: 12 },
  margin: { t: 30, r: 16, b: 40, l: 52 },
  xaxis: { gridcolor: "#2c241b", zerolinecolor: "#3d3225" },
  yaxis: { gridcolor: "#2c241b", zerolinecolor: "#3d3225" },
  colorway: ["#f6a623", "#54c7b0", "#e8746b", "#f8c777", "#2f8f7d", "#a89a85"],
};

export function PlotlyFigure({ figure }) {
  const ref = useRef(null);

  useEffect(() => {
    let node = ref.current;
    let Plotly;
    let cancelled = false;
    (async () => {
      const mod = await import("plotly.js-dist-min");
      Plotly = mod.default || mod;
      if (cancelled || !node) return;
      const layout = { ...DARK_LAYOUT, ...(figure.layout || {}) };
      await Plotly.newPlot(node, figure.data || [], layout, {
        displaylogo: false,
        responsive: true,
        modeBarButtonsToRemove: ["lasso2d", "select2d"],
      });
    })();
    return () => {
      cancelled = true;
      if (node && Plotly) Plotly.purge(node);
    };
  }, [figure]);

  return html`<div class="py-figure" ref=${ref}></div>`;
}
