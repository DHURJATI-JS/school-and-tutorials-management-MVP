/* Dashboard charts (Chart.js) built to the data-viz rules:
   - single series -> validated slot-1 blue (light #2a78d6 / dark #3987e5 from CSS tokens), no legend box
   - bars <= 24px, 4px rounded data end, square at the baseline; 2px lines; end dot with a 2px surface ring
   - hairline solid grid, recessive axes; selective direct labels (latest / extremes only)
   - hover layer: crosshair + value-first tooltip; every chart has a table-view twin
   - dark mode is re-rendered from its own tokens on theme change */
(function () {
  "use strict";
  if (!window.Chart) return;
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const instances = [];
  const inr = (v) => "₹" + Math.round(v).toLocaleString("en-IN");
  const compact = (v) => (v >= 1e7 ? (v / 1e7).toFixed(1) + "Cr" : v >= 1e5 ? (v / 1e5).toFixed(1) + "L" :
    v >= 1e3 ? Math.round(v / 1e3) + "K" : String(Math.round(v)));
  const FORMATS = { inr: inr, pct: (v) => (Math.round(v * 10) / 10) + "%", num: (v) => String(v) };
  const AXIS = { inr: compact, pct: (v) => v + "%", num: (v) => String(v) };

  function tokens() {
    return { series: css("--chart-series-1"), grid: css("--chart-grid"), axis: css("--chart-axis"),
      text: css("--chart-text"), strong: css("--chart-text-strong"), surface: css("--chart-surface") };
  }
  let measurer;
  function fitText(text, maxPx, font) { // ellipsis-truncate to a pixel width (Chart.js clips, it never shortens)
    measurer = measurer || document.createElement("canvas").getContext("2d");
    measurer.font = font;
    if (measurer.measureText(text).width <= maxPx) return text;
    let s = text;
    while (s.length > 1 && measurer.measureText(s + "…").width > maxPx) s = s.slice(0, -1);
    return s.trimEnd() + "…";
  }
  function wash(hex, alpha) {
    const h = hex.replace("#", "");
    const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
    return "rgba(" + ((n >> 16) & 255) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + alpha + ")";
  }

  const crosshair = {
    id: "cnCrosshair",
    afterDatasetsDraw(chart) {
      const active = chart.tooltip && chart.tooltip.getActiveElements && chart.tooltip.getActiveElements();
      if (!active || !active.length || chart.config.type !== "line") return;
      const x = active[0].element.x;
      const area = chart.chartArea;
      const ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = chart.$cnTokens.axis;
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, area.top); ctx.lineTo(x, area.bottom); ctx.stroke();
      ctx.restore();
    },
  };
  const directLabels = {
    id: "cnDirectLabels",
    afterDatasetsDraw(chart) {
      const idxs = chart.$cnLabelIdx || [];
      const meta = chart.getDatasetMeta(0);
      const ctx = chart.ctx;
      const fmt = chart.$cnFmt;
      const horizontal = chart.options.indexAxis === "y";
      ctx.save();
      ctx.font = "600 12px " + (css("--mdb-font-roboto") || "system-ui");
      ctx.fillStyle = chart.$cnTokens.strong;
      idxs.forEach((i) => {
        const el = meta.data[i];
        const value = chart.data.datasets[0].data[i];
        if (!el || value === null || value === undefined) return;
        if (horizontal) { ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.fillText(fmt(value), el.x + 6, el.y); }
        else { ctx.textAlign = "center"; ctx.textBaseline = "bottom"; ctx.fillText(fmt(value), el.x, el.y - 6); }
      });
      ctx.restore();
    },
  };

  function tableTwin(wrap, data, fmt, title) {
    const holder = wrap.querySelector(".cn-chart-table");
    if (!holder) return;
    const table = document.createElement("table");
    table.className = "cn-table";
    const head = table.createTHead().insertRow();
    ["Label", title || "Value"].forEach((t, i) => { const th = document.createElement("th"); th.textContent = t; if (i) th.className = "num"; head.appendChild(th); });
    const body = table.createTBody();
    data.forEach((d) => {
      const row = body.insertRow();
      row.insertCell().textContent = d.label;
      const cell = row.insertCell();
      cell.className = "num";
      cell.textContent = d.value === null || d.value === undefined ? "—" : fmt(d.value);
    });
    holder.replaceChildren(table);
  }

  function build(wrap) {
    const spec = window.CN.readJSON(wrap.dataset.chart);
    const canvas = wrap.querySelector("canvas");
    if (!spec || !canvas) return;
    const t = tokens();
    const data = spec.data || [];
    const fmt = FORMATS[spec.format] || FORMATS.num;
    tableTwin(wrap, data, fmt, spec.valueLabel);
    const values = data.map((d) => d.value);
    const empty = wrap.querySelector(".cn-chart-empty");
    const hasData = values.some((v) => v !== null && v !== undefined && v !== 0);
    if (empty) empty.hidden = hasData;
    canvas.hidden = !hasData;
    if (!hasData) return;
    const labels = data.map((d) => d.label);
    const lastIdx = values.reduce((acc, v, i) => (v !== null && v !== undefined ? i : acc), -1);
    const horizontal = spec.type === "hbar";
    let dataset;
    let labelIdx = [lastIdx];
    if (spec.type === "line") {
      dataset = { data: values, borderColor: t.series, borderWidth: 2, tension: 0.25, fill: true, spanGaps: true,
        backgroundColor: wash(t.series, 0.1), borderCapStyle: "round", borderJoinStyle: "round",
        pointRadius: (c) => (c.dataIndex === lastIdx ? 4 : 0), pointHoverRadius: 5, pointHitRadius: 14,
        pointBackgroundColor: t.series, pointBorderColor: t.surface, pointBorderWidth: 2 };
    } else {
      dataset = { data: values, backgroundColor: t.series, hoverBackgroundColor: wash(t.series, 0.82),
        maxBarThickness: 24, categoryPercentage: 0.72, barPercentage: 0.9, borderSkipped: false,
        borderRadius: horizontal ? { topLeft: 0, bottomLeft: 0, topRight: 4, bottomRight: 4 }
          : { topLeft: 4, topRight: 4, bottomLeft: 0, bottomRight: 0 } };
      if (horizontal) labelIdx = values.length > 1 ? [0, values.length - 1] : [0];
    }
    const valueAxis = { beginAtZero: true, grid: { color: t.grid, lineWidth: 1, drawTicks: false }, border: { display: false },
      ticks: { color: t.text, padding: 8, maxTicksLimit: 5, callback: (v) => (AXIS[spec.format] || AXIS.num)(v),
        font: { size: 11, family: "inherit" } } };
    if (spec.format === "pct") valueAxis.max = 100;
    const catAxis = { grid: { display: false }, border: { color: t.axis }, ticks: { color: t.text, font: { size: 11 },
      maxRotation: 0, autoSkip: true, autoSkipPadding: 12 } };
    if (horizontal) { // the label column gets at most half the plot width - long batch names were cut off at the left edge
      catAxis.ticks.callback = function (value) {
        const room = (this.chart.width - 60) / 2 - 14;
        return fitText(String(this.getLabelForValue(value)), Math.max(40, room), "11px " + window.Chart.defaults.font.family);
      };
    }
    const chart = new window.Chart(canvas, {
      type: spec.type === "line" ? "line" : "bar",
      data: { labels, datasets: [dataset] },
      plugins: [crosshair, directLabels],
      options: {
        indexAxis: horizontal ? "y" : "x",
        responsive: true, maintainAspectRatio: false, animation: { duration: 550 },
        layout: { padding: { top: 22, right: horizontal ? 56 : 14, left: 4 } },
        interaction: spec.type === "line" ? { mode: "index", intersect: false } : { mode: "nearest", axis: horizontal ? "y" : "x", intersect: false },
        scales: horizontal ? { x: valueAxis, y: catAxis } : { x: catAxis, y: valueAxis },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: t.surface, borderColor: t.axis, borderWidth: 1, padding: 10, cornerRadius: 10,
            displayColors: false, titleColor: t.strong, bodyColor: t.text,
            titleFont: { size: 15, weight: "800" }, bodyFont: { size: 12, weight: "500" },
            callbacks: {
              title: (items) => (items.length && items[0].raw !== null ? fmt(items[0].raw) : "No data"),
              label: (item) => item.label + (spec.valueLabel ? " · " + spec.valueLabel : ""),
            },
          },
        },
      },
    });
    chart.$cnTokens = t;
    chart.$cnFmt = fmt;
    chart.$cnLabelIdx = labelIdx.filter((i) => i >= 0);
    chart.update("none");
    instances.push(chart);
  }

  function renderAll() {
    while (instances.length) instances.pop().destroy();
    document.querySelectorAll("[data-chart]").forEach(build);
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-chart-table]");
    if (!btn) return;
    const wrap = btn.closest(".cn-chart-wrap");
    const on = wrap.classList.toggle("show-table");
    btn.textContent = on ? "Chart" : "Table";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  document.addEventListener("cn:theme", () => setTimeout(renderAll, 30));
  renderAll();
})();
