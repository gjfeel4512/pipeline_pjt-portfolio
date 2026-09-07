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
    const tipRect = tip.getBoundingClientRect();
    const offset = 14;

    // 기본은 커서 오른쪽 아래. 화면(뷰포트) 밖으로 나가면 반대쪽으로 붙인다.
    let x = evt.clientX - rootRect.left + offset;
    if (evt.clientX + offset + tipRect.width > window.innerWidth) {
      x = evt.clientX - rootRect.left - offset - tipRect.width;
    }
    if (x < 0) x = 0;

    let y = evt.clientY - rootRect.top + offset;
    if (evt.clientY + offset + tipRect.height > window.innerHeight) {
      y = evt.clientY - rootRect.top - offset - tipRect.height;
    }
    if (y < 0) y = 0;

    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }

  // 막대가 길어서 값 라벨(.viz-value-label)이 막대 끝에 붙어 그려질 때, 그 텍스트가
  // SVG 오른쪽 경계를 넘어가면 브라우저가 잘라버려서(SVG 기본 overflow: hidden)
  // 안 보이는 문제가 있었다(2026-09-07). renderHBarChart 내부에서 만드는 값 라벨뿐
  // 아니라, app.js가 렌더링 이후에 직접 textContent를 덮어쓰는 경우(부호 있는 %,
  // "지난 주 대비" 같은 긴 문구)에는 renderHBarChart가 만들 당시엔 빈 텍스트라 자체
  // 로직만으로는 못 잡아서, 별도로 내보내서 app.js에서도 덮어쓴 직후 호출하게 한다.
  // 값 라벨을 "막대 끝 바깥쪽"에 두는 게 기본인데, 두 경우에 문제가 생긴다:
  //  1) 항목들 값 차이가 커서 막대 하나가 plotW를 거의 다 채우면(1등 항목 등),
  //     그 라벨이 앉을 자리가 SVG 오른쪽 경계 밖으로 나가 잘려서 안 보이던 문제
  //     (2026-09-07 1차 수정 - 왼쪽으로 당기기만 했었음).
  //  2) 그렇게 당기기만 하면 이번엔 라벨이 막대 자체 위에 그대로 겹쳐서, 기본
  //     글자색(--text-primary, 검정 계열)이 막대 색과 뒤섞여 잘 안 보이는("가려짐")
  //     문제가 새로 생김(사용자 리포트, 2026-09-07 2차 수정).
  // 매번 "막대 끝 바깥쪽에 놓았을 때 화면 안에 들어가는가"부터 새로 계산해서:
  //  - 들어가면: 기존처럼 막대 바깥쪽, 기본 글자색, 왼쪽 정렬(막대에서 시작).
  //  - 안 들어가면: 막대 "안쪽"으로 옮기고, 흰색 + 오른쪽 정렬(막대 끝에 붙임)로
  //    바꿔서 막대 색과 상관없이 항상 읽히게 한다 - 물결선(축 압축)까지는 아니어도
  //    "값이 가려서 안 보이는" 문제 자체는 이걸로 해결됨.
  // renderHBarChart가 처음 만들 때 한 번, app.js가 렌더링 이후 텍스트를 다시
  // 덮어쓰는 곳(부호 있는 %, "지난 주 대비" 등)에서 또 한 번 호출되므로, 이전
  // 호출에서 이미 안쪽/바깥쪽으로 바뀐 상태와 상관없이 항상 막대 위치 기준으로
  // 새로 계산한다(멱등성 - 몇 번을 다시 호출해도 같은 결과).
  // 배경색(hex)에 대해 흰 글씨/검은 글씨 중 어느 쪽이 더 잘 보이는지 판단한다.
  // (WCAG 상대휘도 계산 - 값이 높을수록(밝을수록) 검은 글씨가 낫다.) 막대 색은
  // colorVar로 데이터마다/테마(라이트·다크모드)마다 달라질 수 있어서, "안쪽엔
  // 무조건 흰색"으로 고정하면 밝은 색 막대(예: 노란 계열)에서 다시 안 보이는
  // 문제가 재발할 수 있다 - 그래서 막대의 실제 렌더링 색을 읽어 매번 계산한다.
  function pickContrastText(hexColor) {
    const m = /^#?([0-9a-f]{6})$/i.exec((hexColor || "").trim());
    if (!m) return "#fff";
    const int = parseInt(m[1], 16);
    const r = (int >> 16) & 255, g = (int >> 8) & 255, b = int & 255;
    const lin = (c) => {
      const s = c / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    };
    const luminance = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    return luminance > 0.45 ? "#1a1a1a" : "#fff";
  }

  function fixOverflowingValueLabels(root) {
    const svg = root.querySelector("svg");
    if (!svg || !svg.viewBox || !svg.viewBox.baseVal) return;
    const width = svg.viewBox.baseVal.width;
    const margin = 8; // leftPad와 동일한 값 - 양쪽 끝 최소 여백
    const rows = svg.querySelectorAll(".viz-hbar-row");
    const valueLabels = svg.querySelectorAll(".viz-value-label");
    valueLabels.forEach((textEl, i) => {
      if (!textEl.textContent) return;
      const row = rows[i];
      const bar = row ? row.querySelector("rect") : null;
      if (!bar) return;
      const barEnd = parseFloat(bar.getAttribute("x")) + parseFloat(bar.getAttribute("width"));
      let textWidth;
      try {
        textWidth = textEl.getComputedTextLength();
      } catch (e) {
        return; // 아직 렌더링 전이면(레이아웃 미확정) 그냥 둔다
      }
      const outsideX = barEnd + 10;
      if (outsideX + textWidth <= width - margin) {
        // 막대 바깥쪽에 다 들어감 - 기본 배치(대부분의 경우 여기 해당).
        textEl.setAttribute("x", outsideX);
        textEl.removeAttribute("text-anchor");
        textEl.style.fill = "";
      } else {
        // 바깥쪽에 자리가 없음 - 막대 안쪽, 오른쪽 정렬(막대 끝에 붙임)로 옮기고,
        // 막대의 실제 색과 대비되는 글자색(흰색/검은색)을 매번 새로 계산해서 쓴다.
        textEl.setAttribute("x", Math.max(margin, barEnd - 8));
        textEl.setAttribute("text-anchor", "end");
        textEl.style.fill = pickContrastText(bar.getAttribute("fill"));
      }
    });
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

    // tooltipFormatter(d): 툴팁에 보여줄 값을 막대 라벨과 다르게 커스텀하고 싶을 때만
  // 넘긴다(예: 막대/라벨은 %로 보여주되 툴팁은 실제 건수로 보여주는 경우). 안 넘기면
  // 기존처럼 막대 라벨과 같은 값(valueFormatter(d.value) + suffix)을 그대로 보여준다.
  // labelAlign: "end"(기본값, 기존 동작 유지 - 라벨을 막대 쪽에 붙여 오른쪽 정렬)
  // 또는 "start"(라벨을 카드 왼쪽 끝에 붙여 왼쪽 정렬). 라벨 텍스트가 짧을 때
  // "end"로 두면 라벨 칸(labelWidth) 왼쪽에 빈 공간이 많이 남아서 그래프 전체가
  // 왼쪽 끝이 아니라 가운데쯤에 떠 있는 것처럼 보이는 문제가 있어(2026-09-07,
  // "요즘 뜨는 주제" 카드 - 태그가 짧은 개별 태그로 바뀌면서 생김) 이 옵션을
  // 추가했다. 다른 차트는 기본값 그대로라 영향 없음.
  function renderHBarChart(root, { items, valueFormatter = formatCompact, labelWidth = 132, labelAlign = "end", tooltipFormatter = null }) {
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
        x: labelAlign === "start" ? leftPad : labelW - 12,
        y: y + barH / 2 + 5,
        "text-anchor": labelAlign === "start" ? "start" : "end",
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
      // 2026-09-07 3차 수정: 라벨을 막대 "바깥쪽"에 그릴 때는 순서가 상관없었지만,
      // fixOverflowingValueLabels가 라벨을 막대 "안쪽"으로 옮기는 경우 SVG는 그린
      // 순서대로 겹쳐 그리므로(나중에 그린 게 위) 라벨을 막대(trackG)보다 먼저
      // 추가하면 막대가 그 위를 덮어버려 글씨가 안 보이게 된다. 그래서 항상
      // 막대(trackG)를 먼저, 값 라벨을 나중에 추가해서 라벨이 항상 위에 오게 한다.
      svg.appendChild(trackG);
      svg.appendChild(valueLabel);

      const tip = ensureTooltip(root);
      const tooltipValue = tooltipFormatter ? tooltipFormatter(d) : valueFormatter(d.value) + (d.suffix || "");
      const showTip = (evt) => {
        setTooltipRows(tip, d.label, [
          { label: "값", value: tooltipValue, color }
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
    fixOverflowingValueLabels(root);
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

  /* ---------------------------------------------------------------- *
   * History line chart - 카테고리 트렌드 탭의 "지난 1년, 이렇게 흘러왔어요".
   * 2026-09-04: 재생 버튼으로 한 달씩 넘겨보던 방식을 없애고, 완료된 기간 전체를
   * 선 하나로 한 번에 보여주는 정적 차트로 바꿈. 특정 달의 정확한 값은 그 위치에
   * 마우스를 올렸을 때만 세로 안내선(크로스헤어) + 툴팁으로 보여준다(사용자 요청).
   * months: ["2025-09", ...] (집계 중인 달은 호출부에서 이미 제외하고 넘겨줌)
   * series: [{ key, label, colorVar, points: number[] (months와 같은 길이, 값 없으면 null) }]
   * ---------------------------------------------------------------- */
  function fmtMonthShort(ym) {
    // "2025-09" -> "25.09" (연도가 겹치는 두 해를 구분하려면 월만으론 부족해서 2자리 연도 포함)
    return ym.slice(2).replace("-", ".");
  }

  // 점(월)이 비어있는(null) 구간은 선을 잇지 않고 끊어서 그린다 - 카테고리마다
  // 실제 수집된 개월 수가 다를 수 있는데, 그냥 이어버리면 값이 0으로 떨어지는
  // 것처럼 보여서 오해를 준다.
  function buildLineSegments(points) {
    const segments = [];
    let current = [];
    for (let i = 0; i < points.length; i++) {
      const v = points[i];
      if (v === null || v === undefined) {
        if (current.length > 1) segments.push(current);
        current = [];
        continue;
      }
      current.push(i);
    }
    if (current.length > 1) segments.push(current);
    return segments;
  }

  function renderHistoryLineChart(root, { months, series, valueFormatter = formatCompact }) {
    root.innerHTML = "";
    root.style.position = "relative";

    const width = root.clientWidth || 480;
    const height = 260;
    const padL = 44;
    const padR = 16;
    const padT = 16;
    const padB = 28;
    const plotW = width - padL - padR;
    const plotH = height - padT - padB;

    const allPoints = series.flatMap((s) => s.points.filter((v) => v !== null && v !== undefined));
    const maxY = Math.max(1, ...allPoints);
    const n = months.length;
    const xAt = (i) => padL + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    const yAt = (v) => padT + plotH - (Math.max(0, v || 0) / maxY) * plotH;

    const svg = el("svg", {
      width: "100%",
      height,
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": "월별 추이 선 그래프"
    });

    // 가로 격자선 + y축 값 라벨(팀 dataviz 규칙: 1px 헤어라인, 값은 항상 라벨로도 표시)
    [0, 0.5, 1].forEach((t) => {
      const y = padT + plotH * (1 - t);
      svg.appendChild(el("line", { x1: padL, y1: y, x2: padL + plotW, y2: y, class: "viz-gridline" }));
      const label = el("text", { x: padL - 8, y: y + 4, "text-anchor": "end", class: "viz-axis-tick" });
      label.textContent = valueFormatter(maxY * t);
      svg.appendChild(label);
    });

    // x축 월 라벨 - 달이 많으면(9개 초과) 한 칸씩 걸러서 겹치지 않게
    const showEvery = n > 9 ? 2 : 1;
    months.forEach((ym, i) => {
      if (i % showEvery !== 0 && i !== n - 1) return;
      const label = el("text", { x: xAt(i), y: height - 8, "text-anchor": "middle", class: "viz-axis-tick" });
      label.textContent = fmtMonthShort(ym);
      svg.appendChild(label);
    });

    // 크로스헤어(세로 안내선) - 평소엔 숨겨두고, 마우스를 올린 달 위치에서만 보여줌
    const crosshair = el("line", {
      x1: padL, y1: padT, x2: padL, y2: padT + plotH,
      stroke: cssVar("--baseline"), "stroke-width": 1.5, "stroke-dasharray": "3,3"
    });
    crosshair.style.display = "none";
    svg.appendChild(crosshair);

    series.forEach((s) => {
      const color = cssVar(s.colorVar) || cssVar("--series-1");
      buildLineSegments(s.points).forEach((idxs) => {
        const d = idxs.map((i, k) => `${k === 0 ? "M" : "L"}${xAt(i).toFixed(1)},${yAt(s.points[i]).toFixed(1)}`).join(" ");
        svg.appendChild(el("path", { d, fill: "none", stroke: color, "stroke-width": 2.5 }));
      });
      months.forEach((ym, i) => {
        const v = s.points[i];
        if (v === null || v === undefined) return;
        svg.appendChild(el("circle", { cx: xAt(i), cy: yAt(v), r: 3, fill: color }));
      });
    });

    const tip = ensureTooltip(root);

    // 달(월)마다 하나씩, 그 열 전체 높이를 덮는 투명 히트 영역 - 3개 카테고리 선이
    // 겹쳐 있어도 그 달 위 아무 곳에나 마우스를 올리면 한 번에 다 보여주기 위함
    // (점 하나하나를 정확히 맞춰 올려야 하는 방식보다 훨씬 쓰기 편함).
    const colW = n > 1 ? plotW / (n - 1) : plotW;
    months.forEach((ym, i) => {
      const hit = el("rect", {
        x: xAt(i) - colW / 2,
        y: padT,
        width: colW,
        height: plotH,
        fill: "transparent",
        style: "cursor:pointer;"
      });
      const showTip = (evt) => {
        crosshair.setAttribute("x1", xAt(i));
        crosshair.setAttribute("x2", xAt(i));
        crosshair.style.display = "block";

        // 값이 높은 순서로 정렬해서 보여줌(사용자 요청) - 문자열로 포맷하기 전에
        // 숫자 그대로 비교/정렬해야 함(포맷된 문자열은 "1.2M" 같은 식이라 정렬 불가).
        const rows = series
          .filter((s) => s.points[i] !== null && s.points[i] !== undefined)
          .slice()
          .sort((a, b) => b.points[i] - a.points[i])
          .map((s) => ({
            label: s.label,
            value: Math.round(s.points[i]).toLocaleString("ko-KR"),
            color: cssVar(s.colorVar) || cssVar("--series-1")
          }));
        if (rows.length) {
          setTooltipRows(tip, fmtMonthShort(ym), rows);
          tip.style.display = "block";
          positionTooltip(root, tip, evt);
        }
      };
      const hideTip = () => {
        crosshair.style.display = "none";
        clearTooltip(root);
      };
      hit.addEventListener("pointerenter", showTip);
      hit.addEventListener("pointermove", showTip);
      hit.addEventListener("pointerleave", hideTip);
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
    renderLineChart,
    renderHistoryLineChart,
    fixOverflowingValueLabels
  };
})();
