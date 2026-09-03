/**
 * charts.js
 * ---------
 * 의존성 없는 순수 SVG 차트 렌더러 모음입니다 (외부 차트 라이브러리 미사용).
 * frontend/js/charts.js(기존 관리자용 버전)의 렌더러를 기반으로, 이 프로젝트에서
 * 쓰는 두 종류(가로 막대, 히트맵)만 남기고 화면 크기를 키운 버전입니다.
 * 팀 dataviz 가이드 규칙을 따릅니다:
 *  - 바/컬럼 라운드 끝 4px, 베이스라인쪽은 직각
 *  - 격자선은 1px 헤어라인, 실선
 *  - 히트맵은 순차형(sequential) 블루 스케일, 색만이 아니라 값 라벨도 항상 표시
 *  - 툴팁은 보조 수단일 뿐 모든 값은 직접 라벨에서도 확인 가능해야 함
 */
const Charts = (() => {
  const SVG_NS = "http://www.w3.org/2000/svg";

  function el(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  }

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function formatCompact(n) {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1_000) return (n / 1_000).toFixed(1).replace(/\.0$/, "") + "K";
    return String(Math.round(n));
  }

  function clearTooltip(root) {
    const tip = root.querySelector(".viz-tooltip");
    if (tip) tip.style.display = "none";
  }

  function ensureTooltip(root) {
    let tip = root.querySelector(".viz-tooltip");
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "viz-tooltip";
      tip.setAttribute("role", "status");
      root.appendChild(tip);
    }
    return tip;
  }

  function setTooltipRows(tip, titleText, rows) {
    tip.textContent = "";
    const title = document.createElement("div");
    title.className = "viz-tooltip-title";
    title.textContent = titleText;
    tip.appendChild(title);
    rows.forEach((r) => {
      const row = document.createElement("div");
      row.className = "viz-tooltip-row";

      const key = document.createElement("span");
      key.className = "viz-tooltip-key";
      key.style.background = r.color || "transparent";

      const name = document.createElement("span");
      name.className = "viz-tooltip-name";
      name.textContent = r.label;

      const val = document.createElement("strong");
      val.className = "viz-tooltip-value";
      val.textContent = r.value;

      row.appendChild(key);
      row.appendChild(name);
      row.appendChild(val);
      tip.appendChild(row);
    });
  }

  function positionTooltip(root, tip, evt) {
    const rootRect = root.getBoundingClientRect();
    const x = evt.clientX - rootRect.left + 14;
    const y = evt.clientY - rootRect.top + 14;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }

  /* ---------------------------------------------------------------- *
   * Horizontal bar chart - part comparison / magnitude compare
   * items: [{ label, value, colorVar, suffix }]
   * ---------------------------------------------------------------- */
  function estimateTextWidth(text) {
    let w = 0;
    for (const ch of text) w += /[ㄱ-힝]/.test(ch) ? 12 : 7;
    return w;
  }

  function truncateToWidth(text, maxWidth) {
    if (estimateTextWidth(text) <= maxWidth) return text;
    let out = "";
    for (const ch of text) {
      if (estimateTextWidth(out + ch) + 8 > maxWidth) break;
      out += ch;
    }
    return out + "…";
  }

  function renderHBarChart(root, { items, valueFormatter = formatCompact, labelWidth = 132 }) {
    root.innerHTML = "";
    root.style.position = "relative";

    const width = root.clientWidth || 480;
    const rowH = 40;
    const barH = 22;
    const leftPad = 8;
    const labelW = labelWidth;
    const rightPad = 64;
    const height = items.length * rowH + 12;
    const plotW = Math.max(40, width - labelW - rightPad - leftPad);
    const max = Math.max(1, ...items.map((d) => d.value));

    const svg = el("svg", {
      width: "100%",
      height,
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": "가로 막대 차트"
    });

    items.forEach((d, i) => {
      const y = 6 + i * rowH;
      const barW = Math.max(2, (d.value / max) * plotW);
      const color = cssVar(d.colorVar) || cssVar("--series-1");

      const label = el("text", {
        x: labelW - 12,
        y: y + barH / 2 + 5,
        "text-anchor": "end",
        class: "viz-axis-label"
      });
      label.textContent = truncateToWidth(d.label, labelW - 18);
      svg.appendChild(label);

      const trackG = el("g", { class: "viz-hbar-row", tabindex: "0" });

      const bar = el("rect", {
        x: labelW,
        y,
        width: barW,
        height: barH,
        rx: 4,
        fill: color
      });
      trackG.appendChild(bar);

      const hit = el("rect", {
        x: labelW,
        y: y - 4,
        width: plotW + rightPad,
        height: barH + 8,
        fill: "transparent"
      });
      trackG.appendChild(hit);

      const valueLabel = el("text", {
        x: labelW + barW + 10,
        y: y + barH / 2 + 5,
        class: "viz-value-label"
      });
      valueLabel.textContent = valueFormatter(d.value) + (d.suffix || "");
      svg.appendChild(valueLabel);
      svg.appendChild(trackG);

      const tip = ensureTooltip(root);
      const showTip = (evt) => {
        setTooltipRows(tip, d.label, [
          { label: "값", value: valueFormatter(d.value) + (d.suffix || ""), color }
        ]);
        tip.style.display = "block";
        positionTooltip(root, tip, evt);
        bar.setAttribute("opacity", "0.85");
      };
      const hideTip = () => {
        clearTooltip(root);
        bar.setAttribute("opacity", "1");
      };
      trackG.addEventListener("pointermove", showTip);
      trackG.addEventListener("pointerenter", showTip);
      trackG.addEventListener("pointerleave", hideTip);
      trackG.addEventListener("focus", showTip);
      trackG.addEventListener("blur", hideTip);
    });

    root.appendChild(svg);
  }

  function renderLegend(root, items) {
    root.innerHTML = "";
    items.forEach((it) => {
      const chip = document.createElement("span");
      chip.className = "viz-legend-chip";

      const swatch = document.createElement("span");
      swatch.className = "viz-legend-swatch rect";
      swatch.style.background = cssVar(it.colorVar);

      const label = document.createElement("span");
      label.className = "viz-legend-label";
      label.textContent = it.label;

      chip.appendChild(swatch);
      chip.appendChild(label);
      root.appendChild(chip);
    });
  }

  /* ---------------------------------------------------------------- *
   * Heatmap - 요일 그룹 x 시간대, 순차형(1 quantitative var) 블루 스케일
   * dayLabels: ["평일","토요일","일요일"]  slotLabels: ["새벽","오전",...]
   * cells: [{ day: idx, slot: idx, avg_views, avg_duration_sec }]
   * 값(avg_views)으로 색을 정하고, 툴팁에는 조회수와 평균 영상 길이를 함께 보여줍니다.
   * best 셀(최댓값)에는 별 아이콘으로 "가장 반응 좋은 시간대"를 표시합니다.
   * ---------------------------------------------------------------- */
  function lerpColor(hexA, hexB, t) {
    const a = [1, 3, 5].map((i) => parseInt(hexA.slice(i, i + 2), 16));
    const b = [1, 3, 5].map((i) => parseInt(hexB.slice(i, i + 2), 16));
    const c = a.map((v, i) => Math.round(v + (b[i] - v) * t));
    return `rgb(${c[0]},${c[1]},${c[2]})`;
  }

  // 시:분:초 형식. 1시간 미만이면 "분:초"만 표시합니다(짧은 영상에서 "0:05:23"처럼
  // 불필요한 "0:" 접두사가 붙지 않도록).
  function fmtDuration(sec) {
    sec = Math.round(sec);
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    if (h > 0) {
      return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    }
    return `${m}:${String(s).padStart(2, "0")}`;
  }

  function renderHeatmap(root, { dayLabels, slotLabels, cells }) {
    root.innerHTML = "";
    root.style.position = "relative";

    const SCALE_LOW = "#cde2fb";
    const SCALE_HIGH = "#0d366b";

    const width = root.clientWidth || 640;
    const padL = 78;
    const padT = 28;
    const padR = 8;
    const padB = 4;
    const cellGap = 8;
    const plotW = width - padL - padR;
    const cellW = (plotW - cellGap * (slotLabels.length - 1)) / slotLabels.length;
    const cellH = 58;
    const plotH = (cellH + cellGap) * dayLabels.length - cellGap;
    const height = padT + plotH + padB;

    // avg_views가 null/undefined인 셀은 그 구간에 표본이 없다는 뜻(nodata)이라
    // 색상 스케일 계산과 "베스트 셀" 후보에서 제외합니다.
    const validCells = cells.filter((c) => c.avg_views !== null && c.avg_views !== undefined);
    const values = validCells.map((c) => c.avg_views);
    const minV = values.length ? Math.min(...values) : 0;
    const maxV = values.length ? Math.max(...values) : 0;
    const range = maxV - minV || 1;
    const bestKey = validCells.length
      ? validCells.reduce((best, c) => (c.avg_views > best.avg_views ? c : best), validCells[0])
      : null;

    const svg = el("svg", {
      width: "100%",
      height,
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": "요일-시간대 업로드 히트맵"
    });

    slotLabels.forEach((s, i) => {
      const x = padL + i * (cellW + cellGap) + cellW / 2;
      const t = el("text", { x, y: padT - 10, "text-anchor": "middle", class: "viz-axis-tick" });
      t.textContent = s;
      svg.appendChild(t);
    });

    dayLabels.forEach((d, i) => {
      const y = padT + i * (cellH + cellGap) + cellH / 2 + 5;
      const t = el("text", { x: padL - 12, y, "text-anchor": "end", class: "viz-axis-label" });
      t.textContent = d;
      svg.appendChild(t);
    });

    const tip = ensureTooltip(root);
    const cellByKey = new Map(cells.map((c) => [`${c.day}-${c.slot}`, c]));

    dayLabels.forEach((dLabel, di) => {
      slotLabels.forEach((sLabel, si) => {
        const c = cellByKey.get(`${di}-${si}`);
        if (!c) return;
        const v = c.avg_views;
        const noData = v === null || v === undefined;
        const t = !noData && range ? (v - minV) / range : 0;
        const color = noData ? "var(--surface-1, #e7ebf1)" : lerpColor(SCALE_LOW, SCALE_HIGH, t);
        const x = padL + si * (cellW + cellGap);
        const y = padT + di * (cellH + cellGap);
        const isBest = !noData && c === bestKey;
        const textColor = noData ? "var(--text-muted, #8a93a3)" : t > 0.55 ? "#ffffff" : "#0b0b0b";

        const g = el("g", { tabindex: "0", style: "cursor:pointer;" });

        const rect = el("rect", {
          x, y, width: cellW, height: cellH, rx: 8, fill: color
        });
        if (noData) {
          rect.setAttribute("stroke", "var(--border, #c7cdd6)");
          rect.setAttribute("stroke-width", "1");
          rect.setAttribute("stroke-dasharray", "4 3");
        }
        if (isBest) {
          rect.setAttribute("stroke", "#0b0b0b");
          rect.setAttribute("stroke-width", "2");
        }
        g.appendChild(rect);

        const valueText = el("text", {
          x: x + cellW / 2,
          y: y + cellH / 2 + 6,
          "text-anchor": "middle",
          class: "heatmap-cell-value",
          fill: textColor
        });
        valueText.textContent = noData ? "nodata" : v.toLocaleString("ko-KR");
        g.appendChild(valueText);

        if (isBest) {
          const star = el("text", {
            x: x + cellW - 14,
            y: y + 20,
            "text-anchor": "middle",
            class: "heatmap-star"
          });
          star.textContent = "★";
          g.appendChild(star);
        }

        const showTip = (evt) => {
          const rows = noData
            ? [{ label: "표본", value: "아직 데이터 없음 (nodata)", color: "transparent" }]
            : [
                { label: "평균 조회수", value: v.toLocaleString("ko-KR") + "회", color },
                { label: "평균 영상 길이", value: fmtDuration(c.avg_duration_sec), color: "transparent" }
              ];
          setTooltipRows(tip, `${dLabel} · ${sLabel}`, rows);
          tip.style.display = "block";
          positionTooltip(root, tip, evt);
        };
        const hideTip = () => clearTooltip(root);

        g.addEventListener("pointerenter", showTip);
        g.addEventListener("pointermove", showTip);
        g.addEventListener("pointerleave", hideTip);
        g.addEventListener("focus", showTip);
        g.addEventListener("blur", hideTip);

        svg.appendChild(g);
      });
    });

    root.appendChild(svg);

    const scaleWrap = document.createElement("div");
    scaleWrap.className = "viz-heat-scale";
    const lowText = document.createElement("span");
    lowText.textContent = `반응 적음 (${minV.toLocaleString("ko-KR")}회)`;
    const bar = document.createElement("span");
    bar.className = "viz-heat-scale-bar";
    bar.style.background = `linear-gradient(90deg, ${SCALE_LOW}, ${SCALE_HIGH})`;
    const highText = document.createElement("span");
    highText.textContent = `반응 많음 (${maxV.toLocaleString("ko-KR")}회)`;
    const starNote = document.createElement("span");
    starNote.className = "viz-heat-star-note";
    starNote.innerHTML = `<span class="heatmap-star" style="font-size:14px;">★</span> 가장 반응 좋은 시간대`;
    scaleWrap.appendChild(lowText);
    scaleWrap.appendChild(bar);
    scaleWrap.appendChild(highText);
    scaleWrap.appendChild(starNote);
    root.appendChild(scaleWrap);
  }

  /* ---------------------------------------------------------------- *
   * Line chart - 여러 시리즈(곡선)를 같은 x축(days) 위에 겹쳐 그림.
   * series: [{ label, colorVar, points: [y0, y1, ...] }] (points는 x=days[i] 순서와 대응)
   * 값은 0~1로 정규화돼 있다고 가정(성장곡선 "모양" 비교용 - 절대 조회수 아님).
   * ---------------------------------------------------------------- */
  function renderLineChart(root, { days, series, xLabel = "", yLabel = "" }) {
    root.innerHTML = "";
    root.style.position = "relative";

    const width = root.clientWidth || 480;
    const height = 220;
    const padL = 36;
    const padR = 12;
    const padT = 16;
    const padB = 28;
    const plotW = width - padL - padR;
    const plotH = height - padT - padB;

    const maxY = Math.max(0.001, ...series.flatMap((s) => s.points));
    const minX = Math.min(...days);
    const maxX = Math.max(...days);
    const xAt = (d) => padL + ((d - minX) / (maxX - minX || 1)) * plotW;
    const yAt = (v) => padT + plotH - (v / maxY) * plotH;

    const svg = el("svg", {
      width: "100%",
      height,
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": "시계열 라인 차트"
    });

    // 격자선(가로 3줄) - 팀 dataviz 규칙(1px 헤어라인)
    [0, 0.5, 1].forEach((t) => {
      const y = padT + plotH * (1 - t);
      svg.appendChild(el("line", { x1: padL, y1: y, x2: padL + plotW, y2: y, class: "viz-gridline" }));
    });

    // x축 라벨(시작/끝만 - 30일치를 다 찍으면 겹쳐서 안 보임)
    [minX, maxX].forEach((d) => {
      const t = el("text", { x: xAt(d), y: height - 6, "text-anchor": "middle", class: "viz-axis-tick" });
      t.textContent = `${xLabel}${d}${xLabel ? "" : "일"}`;
      svg.appendChild(t);
    });

    const tip = ensureTooltip(root);

    series.forEach((s) => {
      const color = cssVar(s.colorVar) || cssVar("--series-1");
      const pathD = days
        .map((d, i) => `${i === 0 ? "M" : "L"}${xAt(d).toFixed(1)},${yAt(s.points[i]).toFixed(1)}`)
        .join(" ");
      svg.appendChild(el("path", { d: pathD, fill: "none", stroke: color, "stroke-width": 2.5 }));

      // 각 점마다 투명한 히트 영역을 둬서 호버 시 그 시리즈 값을 보여줌(마지막 점 기준 대표 라벨)
      const lastIdx = days.length - 1;
      const dot = el("circle", { cx: xAt(days[lastIdx]), cy: yAt(s.points[lastIdx]), r: 4, fill: color });
      svg.appendChild(dot);

      const hit = el("circle", { cx: xAt(days[lastIdx]), cy: yAt(s.points[lastIdx]), r: 10, fill: "transparent", style: "cursor:pointer;" });
      const showTip = (evt) => {
        setTooltipRows(tip, s.label, [
          { label: `${yLabel || "값"}(정규화)`, value: s.points[lastIdx].toFixed(2), color }
        ]);
        tip.style.display = "block";
        positionTooltip(root, tip, evt);
      };
      hit.addEventListener("pointerenter", showTip);
      hit.addEventListener("pointermove", showTip);
      hit.addEventListener("pointerleave", () => clearTooltip(root));
      svg.appendChild(hit);
    });

    root.appendChild(svg);

    const legend = document.createElement("div");
    legend.className = "viz-legend";
    series.forEach((s) => {
      const chip = document.createElement("span");
      chip.className = "viz-legend-chip";
      const swatch = document.createElement("span");
      swatch.className = "viz-legend-swatch rect";
      swatch.style.background = cssVar(s.colorVar);
      const label = document.createElement("span");
      label.className = "viz-legend-label";
      label.textContent = s.label;
      chip.appendChild(swatch);
      chip.appendChild(label);
      legend.appendChild(chip);
    });
    root.appendChild(legend);
  }

  return {
    formatCompact,
    fmtDuration,
    renderHBarChart,
    renderLegend,
    renderHeatmap,
    renderLineChart
  };
})();
