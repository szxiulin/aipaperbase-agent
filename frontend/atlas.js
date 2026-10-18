// Ask page + area map. Both read only /api/cap/* so every number matches MCP and HTTP.
(function () {
  const $ = (selector) => document.querySelector(selector);
  const fmt = (value) => new Intl.NumberFormat("zh-CN").format(value ?? 0);
  const esc = (value = "") => String(value).replace(/[&<>'"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[c]));
  const FULL_YEARS = [2023, 2024, 2025];
  const isEn = () => Boolean(window.I18N && window.I18N.isEn());
  const L = (zh, en) => (isEn() ? en : zh);
  const nm = (item) => (isEn() && item && item.name_en ? item.name_en : (item && item.name_zh) || "");
  const ALL_YEARS = [2023, 2024, 2025, 2026];

  async function cap(name, args) {
    const response = await fetch(`/api/cap/${name}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(args || {}) });
    const payload = await response.json();
    if (!payload.ok) throw new Error(payload.error || L(`${name} 调用失败`, `${name} failed`));
    return payload;
  }

  // ---------- shared data ----------
  let taxonomyPromise = null;
  function taxonomy() {
    if (!taxonomyPromise) {
      taxonomyPromise = Promise.all([cap("list_taxonomy", {}), cap("cooccurrence", { field_a: "area", field_b: "year", limit: 500 }), fetch("/api/venues").then((r) => r.json())])
        .then(([tax, years, venues]) => {
          const areas = tax.data.areas.map((area) => ({ ...area, years: {} }));
          const byId = Object.fromEntries(areas.map((a) => [a.area_id, a]));
          const dirs = {};
          areas.forEach((area) => area.directions.forEach((d) => { dirs[d.direction_id] = { ...d, area_id: area.area_id }; }));
          years.data.items.forEach((row) => { if (byId[row.a]) byId[row.a].years[row.b] = row.count; });
          const total = areas.reduce((sum, a) => sum + a.entity_count, 0);
          return { areas, byId, dirs, total, meta: tax.meta, venues: (venues.items || []).map((v) => v.venue) };
        })
        .catch((error) => { taxonomyPromise = null; throw error; });
    }
    return taxonomyPromise;
  }

  function growth(years) {
    const first = years[FULL_YEARS[0]] || 0;
    const last = years[FULL_YEARS[FULL_YEARS.length - 1]] || 0;
    if (!first) return null;
    return (last - first) / first;
  }

  function growthText(value) {
    if (value === null || value === undefined) return "—";
    const pct = Math.round(value * 100);
    return `${pct > 0 ? "+" : ""}${pct}%`;
  }

  function growthClass(value) {
    if (value === null || value === undefined) return "";
    return value > 0.15 ? "up" : value < -0.15 ? "down" : "flat";
  }

  function spark(years) {
    const max = Math.max(...ALL_YEARS.map((y) => years[y] || 0), 1);
    return `<span class="spark" aria-hidden="true">${ALL_YEARS.map((y) => `<i class="${y === 2026 ? "partial" : ""}" style="height:${Math.max(6, ((years[y] || 0) / max) * 100)}%"></i>`).join("")}</span>`;
  }

  function yearBars(years) {
    const max = Math.max(...ALL_YEARS.map((y) => years[y] || 0), 1);
    return `<div class="year-bars">${ALL_YEARS.map((y) => `<div class="year-bar ${y === 2026 ? "partial" : ""}"><b>${fmt(years[y] || 0)}</b><i style="height:${Math.max(3, ((years[y] || 0) / max) * 100)}%"></i><span>${y}${y === 2026 ? "*" : ""}</span></div>`).join("")}</div>
      <p class="fine-print">${L("* 2026 年还在收录中，不和完整年份比较。增长率按 2023 → 2025 计算。", "* 2026 is still being collected and is not compared with full years. Growth is 2023 → 2025.")}</p>`;
  }

  function rankBars(items) {
    const max = Math.max(...items.map((x) => x.count), 1);
    return `<div class="rank-bars">${items.map((x) => `<div class="rank-row"><span class="rank-label" title="${esc(x.label)}">${esc(x.label)}</span><span class="rank-track"><i style="width:${Math.max(1.5, (x.count / max) * 100)}%"></i></span><b>${fmt(x.count)}</b></div>`).join("")}</div>`;
  }

  function dataNote(meta, extra = []) {
    const acc = meta && meta.area_accuracy ? `${(meta.area_accuracy * 100).toFixed(1)}%` : "—";
    const lines = [
      ...extra,
      L("每篇论文只计一次：同一论文的预印本和正式发表已合并，按正式发表的会议与年份统计。", "Each paper counts once: preprints and published versions are merged and counted by the published venue and year."),
      L("编委名单、目录、勘误等 251 条非论文记录不计入。", "251 non-paper records (committee lists, front matter, errata) are excluded."),
      L(`领域与方向由大模型标注；在 399 篇抽样上做过复核，领域准确率约 ${acc}。复核由 AI 代理完成并以系统标签为参照，不是独立的人工评测。`, `Areas and directions are labeled by LLMs. A 399-paper sample was reviewed; area accuracy is about ${acc}. The review was done by an AI agent against the system labels, not by independent human annotators.`),
      L(`数据版本：${esc(meta?.areas_version || "")} / ${esc(meta?.directions_version || "")}。`, `Data version: ${esc(meta?.areas_version || "")} / ${esc(meta?.directions_version || "")}.`),
    ];
    return `<details class="data-note"><summary>ⓘ ${L("数据说明", "About this data")}</summary><ul>${lines.map((l) => `<li>${l}</li>`).join("")}</ul></details>`;
  }

  // ---------- paper list ----------
  const GREEK = { alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", theta: "θ", lambda: "λ", mu: "μ", pi: "π", sigma: "σ", tau: "τ", phi: "φ", omega: "ω", ell: "ℓ", infty: "∞" };
  // Titles come from proceedings metadata and may carry inline LaTeX or HTML.
  function cleanTitle(title) {
    return String(title || "")
      .replace(/<\/?[a-z]+>/gi, "")
      .replace(/\\([a-zA-Z]+)/g, (m, name) => GREEK[name] || m)
      .replace(/\\[a-zA-Z]+\{([^}]*)\}/g, "$1")
      .replace(/[$^{}_]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function paperList(items, names) {
    if (!items.length) return `<p class="muted">${L("没有符合条件的论文。", "No matching papers.")}</p>`;
    return `<ul class="paper-list">${items.map((p) => `<li class="paper-row" data-entity="${esc(p.entity_id)}">
      <button class="paper-toggle" type="button" aria-expanded="false">
        <strong>${esc(cleanTitle(p.title))}</strong>
        <span class="paper-meta">${esc(p.venue || "")} ${p.year || ""}${p.direction_id && names[p.direction_id] ? ` · ${esc(names[p.direction_id])}` : ""}</span>
      </button>
      <div class="paper-more" hidden></div>
    </li>`).join("")}</ul>`;
  }

  function bindPaperList(container) {
    if (container.dataset.papersBound) return;
    container.dataset.papersBound = "1";
    container.addEventListener("click", async (event) => {
      const toggle = event.target.closest(".paper-toggle");
      if (!toggle) return;
      const row = toggle.closest(".paper-row");
      const more = row.querySelector(".paper-more");
      const open = more.hidden;
      more.hidden = !open;
      toggle.setAttribute("aria-expanded", String(open));
      if (!open || more.dataset.loaded) return;
      more.innerHTML = `<p class="muted">${L("读取中…", "Loading…")}</p>`;
      try {
        const { data } = await cap("get_paper", { entity_id: row.dataset.entity });
        const link = data.paper_url || (data.doi ? `https://doi.org/${data.doi}` : data.arxiv_id ? `https://arxiv.org/abs/${data.arxiv_id}` : "");
        more.dataset.loaded = "1";
        more.innerHTML = `<p class="paper-authors">${esc(data.authors || L("作者未知", "Unknown authors"))}</p>
          ${data.abstract ? `<p>${esc(data.abstract)}</p>` : `<p class="muted">${L("没有找到可追溯的摘要。", "No traceable abstract.")}</p>`}
          <p class="paper-links">${link ? `<a href="${esc(link)}" target="_blank" rel="noreferrer">${L("打开原文", "Open paper")} ↗</a>` : ""}
          ${data.labels ? `<span class="muted">${L("领域", "Area")}: ${esc(data.labels.area_name || "")}${data.labels.direction_name ? ` · ${L("方向", "Direction")}: ${esc(data.labels.direction_name)}` : ""}</span>` : ""}</p>`;
      } catch (error) {
        more.innerHTML = `<p class="muted">${L("读取失败", "Failed to load")}: ${esc(error.message)}</p>`;
      }
    });
  }

  async function pagedPapers(container, filters, names, page = 1) {
    container.innerHTML = `<p class="muted">${L("读取论文…", "Loading papers…")}</p>`;
    const { data } = await cap("search_papers", { filters, page, page_size: 10 });
    container.innerHTML = `${paperList(data.items, names)}
      ${data.total_pages > 1 ? `<div class="pager"><button class="secondary-button" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>${L("上一页", "Previous")}</button><span>${L(`第 ${page} / ${fmt(data.total_pages)} 页`, `Page ${page} / ${fmt(data.total_pages)}`)}</span><button class="secondary-button" data-page="${page + 1}" ${page >= data.total_pages ? "disabled" : ""}>${L("下一页", "Next")}</button></div>` : ""}`;
    bindPaperList(container);
    container.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => {
      pagedPapers(container, filters, names, Number(button.dataset.page)).catch(showFailure(container));
    }));
  }

  function showFailure(container) {
    return (error) => { container.innerHTML = `<p class="muted">${L("读取失败", "Failed to load")}: ${esc(error.message || String(error))}. ${L("确认本地服务已启动。", "Check that the local server is running.")}</p>`; };
  }

  function nameMap(tax) {
    const names = {};
    tax.areas.forEach((a) => { names[a.area_id] = nm(a); a.directions.forEach((d) => { names[d.direction_id] = nm(d); }); });
    return names;
  }

  // ---------- area map ----------
  const go = (...parts) => window.navigate("map", parts);

  function crumbs(items) {
    $("#map-crumbs").innerHTML = items.map((item, i) => i === items.length - 1
      ? `<span aria-current="page">${esc(item.label)}</span>`
      : `<a href="${item.href}">${esc(item.label)}</a><span class="crumb-sep">›</span>`).join("");
  }

  function setHeading(title, subtitle) {
    $("#page-title").textContent = title;
    const sub = $("#page-subtitle");
    if (sub) sub.textContent = subtitle;
  }

  async function renderMapHome(body) {
    const tax = await taxonomy();
    setHeading(L("领域地图", "Area map"), L("先选一个领域，再看它下面的方向和论文。", "Pick an area, then explore its directions and papers."));
    crumbs([{ label: L("全部领域", "All areas") }]);
    const areas = tax.areas.filter((a) => a.area_id !== "other").sort((a, b) => b.entity_count - a.entity_count);
    const other = tax.byId.other;
    body.innerHTML = `
      <div class="map-legend">
        <span><b>${L("领域", "Area")}</b> ${L(`大类，共 ${areas.length} 个`, `broad field, ${areas.length} in total`)}</span>
        <span><b>${L("方向", "Direction")}</b> ${L(`领域里的细分主题，共 ${fmt(Object.keys(tax.dirs).length)} 个`, `sub-topic within an area, ${fmt(Object.keys(tax.dirs).length)} in total`)}</span>
        <span class="legend-treemap">${L("方块面积 = 论文数 · 每个领域有固定颜色", "Tile area = paper count · each area keeps its own color")}</span>
      </div>
      <div class="treemap" id="treemap" role="list"></div>
      <div class="area-list" role="list">${areas.map((a) => {
        const g = growth(a.years);
        return `<a role="listitem" class="area-row" href="#/map/${encodeURIComponent(a.area_id)}" style="--area:${areaColor(tax, a)}">
          <span class="swatch"></span><span class="area-row-name">${esc(nm(a))}</span>
          <span class="area-row-count">${fmt(a.entity_count)}</span><span class="growth ${growthClass(g)}">${growthText(g)}</span></a>`;
      }).join("")}</div>
      <p class="fine-print">${L("增长按 2023 → 2025 计算（2026 年还在收录中）。", "Growth is 2023 → 2025 (2026 is still being collected).")}${other ? L(`另有 ${fmt(other.entity_count)} 篇无法归入任何领域，记为“其他”，未画出。`, ` Another ${fmt(other.entity_count)} papers fit no area ("other") and are not drawn.`) : ""}</p>
      ${dataNote(tax.meta)}`;
    drawTreemap($("#treemap"), tax, areas);
    if (!window.__treemapResize) {
      window.__treemapResize = true;
      let timer = 0;
      window.addEventListener("resize", () => {
        clearTimeout(timer);
        timer = setTimeout(() => { const el = $("#treemap"); if (el && el.offsetParent) taxonomy().then((t) => drawTreemap(el, t, t.areas.filter((x) => x.area_id !== "other").sort((x, y) => y.entity_count - x.entity_count))); }, 150);
      });
    }
  }

  // ---------- treemap ----------
  function areaColor(tax, area) {
    return `var(--area-${tax.areas.indexOf(area) % 21})`;
  }

  function tileInk(el) {
    const css = getComputedStyle(el).backgroundColor;
    const rgb = css.match(/[\d.]+/g);
    if (!rgb) return "#fff";
    const unit = css.startsWith("color(") ? 1 : 255;  // color() syntax reports channels in 0..1
    const [r, g, b] = rgb.slice(0, 3).map((v) => { v = Number(v) / unit; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.32 ? "#111111" : "#ffffff";
  }

  // Squarified treemap (Bruls et al.): rows of tiles with aspect ratios close to 1.
  function squarify(values, x, y, w, h) {
    const total = values.reduce((sum, v) => sum + v.value, 0);
    const scale = (w * h) / total;
    const items = values.map((v) => ({ ...v, area: v.value * scale }));
    const out = [];
    let rect = { x, y, w, h };
    let row = [];
    const worst = (r, side) => {
      const sum = r.reduce((s2, i) => s2 + i.area, 0);
      const max = Math.max(...r.map((i) => i.area));
      const min = Math.min(...r.map((i) => i.area));
      return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
    };
    const layout = (r) => {
      const sum = r.reduce((s2, i) => s2 + i.area, 0);
      if (rect.w >= rect.h) {
        const cw = sum / rect.h; let cy = rect.y;
        r.forEach((i) => { const ch = i.area / cw; out.push({ ...i, x: rect.x, y: cy, w: cw, h: ch }); cy += ch; });
        rect = { x: rect.x + cw, y: rect.y, w: rect.w - cw, h: rect.h };
      } else {
        const ch = sum / rect.w; let cx = rect.x;
        r.forEach((i) => { const cw = i.area / ch; out.push({ ...i, x: cx, y: rect.y, w: cw, h: ch }); cx += cw; });
        rect = { x: rect.x, y: rect.y + ch, w: rect.w, h: rect.h - ch };
      }
    };
    items.forEach((item) => {
      const side = Math.min(rect.w, rect.h);
      if (!row.length || worst([...row, item], side) <= worst(row, side)) row.push(item);
      else { layout(row); row = [item]; }
    });
    if (row.length) layout(row);
    return out;
  }

  function drawTreemap(el, tax, areas) {
    if (!el) return;
    const width = el.clientWidth;
    if (!width) return;
    const height = Math.round(Math.max(420, Math.min(640, width * 0.52)));
    el.style.height = `${height}px`;
    const gap = 3;
    const tiles = squarify(areas.map((a) => ({ value: a.entity_count, area_ref: a })), 0, 0, width, height);
    el.innerHTML = tiles.map((t) => {
      const a = t.area_ref;
      const g = growth(a.years);
      const size = t.w > 150 && t.h > 96 ? "lg" : t.w > 92 && t.h > 58 ? "md" : "sm";
      return `<a role="listitem" class="tile tile-${size}" href="#/map/${encodeURIComponent(a.area_id)}" title="${esc(nm(a))} · ${fmt(a.entity_count)} ${L("篇", "papers")} · ${growthText(g)}"
        style="left:${t.x + gap / 2}px;top:${t.y + gap / 2}px;width:${Math.max(0, t.w - gap)}px;height:${Math.max(0, t.h - gap)}px;--area:${areaColor(tax, a)}">
        <span class="tile-name">${esc(nm(a))}</span>
        <span class="tile-num">${fmt(a.entity_count)}</span>
        <span class="tile-growth">${growthText(g)}</span>
      </a>`;
    }).join("");
    el.querySelectorAll(".tile").forEach((tile) => { tile.style.color = tileInk(tile); });
  }

  async function renderArea(body, areaId) {
    const tax = await taxonomy();
    const area = tax.byId[areaId];
    if (!area) { body.innerHTML = `<p class="muted">${L("没有这个领域。", "No such area.")}</p>`; return; }
    const names = nameMap(tax);
    setHeading(nm(area), L(`${fmt(area.entity_count)} 篇论文 · ${area.directions.length} 个方向`, `${fmt(area.entity_count)} papers · ${area.directions.length} directions`));
    body.style.setProperty("--area", areaColor(tax, area));
    crumbs([{ label: L("全部领域", "All areas"), href: "#/map" }, { label: nm(area) }]);
    const g = growth(area.years);
    body.innerHTML = `
      <div class="map-split">
        <article class="panel"><h2 class="block-title">${L("每年论文数", "Papers per year")}</h2>${yearBars(area.years)}
          <p class="headline-stat">2023 → 2025：<span class="growth ${growthClass(g)}">${growthText(g)}</span></p></article>
        <article class="panel"><h2 class="block-title">${L("主要发表在", "Top venues")}</h2><div id="area-venues"><p class="muted">${L("读取中…", "Loading…")}</p></div></article>
      </div>
      <article class="panel">
        <div class="block-head"><h2 class="block-title">${L("方向", "Directions")}</h2><span class="muted">${L("点一个方向，看它的趋势和论文", "Open a direction to see its trend and papers")}</span></div>
        <div id="area-directions"><p class="muted">${L("读取中…", "Loading…")}</p></div>
      </article>
      <article class="panel">
        <div class="block-head"><h2 class="block-title">${L("论文", "Papers")}</h2><a class="text-link" href="#/papers/${encodeURIComponent(areaId)}">${L("在论文页里搜索这个领域", "Search this area on the Papers page")} →</a></div>
        <div id="area-papers"></div>
      </article>
      ${dataNote(tax.meta)}`;
    const filters = { area_ids: [areaId] };
    cap("top_values", { filters, field: "venue", limit: 6 }).then(({ data }) => {
      $("#area-venues").innerHTML = rankBars(data.items.map((x) => ({ label: x.key, count: x.count })));
    }).catch(showFailure($("#area-venues")));
    cap("cooccurrence", { filters, field_a: "direction", field_b: "year", limit: 500 }).then(({ data }) => {
      const years = {};
      data.items.forEach((row) => { (years[row.a] = years[row.a] || {})[row.b] = row.count; });
      const dirs = [...area.directions].sort((a, b) => b.entity_count - a.entity_count);
      const max = Math.max(...dirs.map((d) => d.entity_count), 1);
      $("#area-directions").innerHTML = `<div class="dir-table" role="list">${dirs.map((d) => {
        const dg = growth(years[d.direction_id] || {});
        return `<a role="listitem" class="dir-row" href="#/map/${encodeURIComponent(areaId)}/${encodeURIComponent(d.direction_id)}">
          <span class="dir-name">${esc(nm(d))}</span>
          <span class="dir-bar"><i style="width:${Math.max(1.5, (d.entity_count / max) * 100)}%"></i></span>
          <span class="dir-count">${fmt(d.entity_count)}</span>
          ${spark(years[d.direction_id] || {})}
          <span class="growth ${growthClass(dg)}">${growthText(dg)}</span>
        </a>`;
      }).join("")}</div>`;
    }).catch(showFailure($("#area-directions")));
    pagedPapers($("#area-papers"), filters, names).catch(showFailure($("#area-papers")));
  }

  async function renderDirection(body, areaId, directionId) {
    const tax = await taxonomy();
    const dir = tax.dirs[directionId];
    const area = tax.byId[areaId] || (dir && tax.byId[dir.area_id]);
    if (!dir || !area) { body.innerHTML = `<p class="muted">${L("没有这个方向。", "No such direction.")}</p>`; return; }
    const names = nameMap(tax);
    setHeading(nm(dir), L(`${nm(area)} 领域下的方向 · ${fmt(dir.entity_count)} 篇论文`, `Direction in ${nm(area)} · ${fmt(dir.entity_count)} papers`));
    body.style.setProperty("--area", areaColor(tax, area));
    crumbs([{ label: L("全部领域", "All areas"), href: "#/map" }, { label: nm(area), href: `#/map/${encodeURIComponent(area.area_id)}` }, { label: nm(dir) }]);
    const siblings = area.directions.filter((d) => d.direction_id !== directionId).sort((a, b) => b.entity_count - a.entity_count).slice(0, 8);
    body.innerHTML = `
      <div class="map-split">
        <article class="panel"><h2 class="block-title">${L("每年论文数", "Papers per year")}</h2><div id="dir-trend"><p class="muted">${L("读取中…", "Loading…")}</p></div></article>
        <article class="panel"><h2 class="block-title">${L("主要发表在", "Top venues")}</h2><div id="dir-venues"><p class="muted">${L("读取中…", "Loading…")}</p></div></article>
      </div>
      <article class="panel">
        <div class="block-head"><h2 class="block-title">${L("论文", "Papers")}</h2><span class="muted">${L("按年份从新到旧；点标题看摘要", "Newest first; click a title for the abstract")}</span></div>
        <div id="dir-papers"></div>
      </article>
      ${siblings.length ? `<div class="sibling-chips"><span class="muted">${L("同领域的其他方向：", "Other directions in this area:")}</span>${siblings.map((d) => `<a class="chip" href="#/map/${encodeURIComponent(area.area_id)}/${encodeURIComponent(d.direction_id)}">${esc(nm(d))} <b>${fmt(d.entity_count)}</b></a>`).join("")}</div>` : ""}
      ${dataNote(tax.meta)}`;
    const filters = { direction_ids: [directionId] };
    cap("trend", { filters }).then(({ data }) => {
      const years = Object.fromEntries((data.items || []).map((x) => [x.key, x.count]));
      const g = growth(years);
      $("#dir-trend").innerHTML = `${yearBars(years)}<p class="headline-stat">2023 → 2025：<span class="growth ${growthClass(g)}">${growthText(g)}</span></p>`;
    }).catch(showFailure($("#dir-trend")));
    cap("top_values", { filters, field: "venue", limit: 6 }).then(({ data }) => {
      $("#dir-venues").innerHTML = rankBars(data.items.map((x) => ({ label: x.key, count: x.count })));
    }).catch(showFailure($("#dir-venues")));
    pagedPapers($("#dir-papers"), filters, names).catch(showFailure($("#dir-papers")));
  }

  function showMap(areaId, directionId) {
    const body = $("#map-body");
    body.style.removeProperty("--area");
    body.dataset.level = directionId ? "direction" : areaId ? "area" : "home";
    body.innerHTML = `<p class="muted">${L("读取中…", "Loading…")}</p>`;
    const task = directionId ? renderDirection(body, areaId, directionId) : areaId ? renderArea(body, areaId) : renderMapHome(body);
    task.catch(showFailure(body));
    window.scrollTo({ top: 0 });
  }

  async function fillAreaSelect(select) {
    if (!select || select.dataset.filled) return;
    const tax = await taxonomy();
    select.dataset.filled = "1";
    const current = select.value;
    select.innerHTML = `<option value="">${L("全部领域", "All areas")}</option>${tax.areas.filter((a) => a.area_id !== "other").sort((a, b) => b.entity_count - a.entity_count).map((a) => `<option value="${esc(a.area_id)}">${esc(nm(a))}</option>`).join("")}`;
    select.value = current;
  }

  // Theme switches change tile colours; recompute the label ink.
  new MutationObserver(() => {
    const el = $("#treemap");
    if (el && el.offsetParent) taxonomy().then((t) => drawTreemap(el, t, t.areas.filter((x) => x.area_id !== "other").sort((x, y) => y.entity_count - x.entity_count)));
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  document.addEventListener("apex:langchange", () => {
    const map = document.getElementById("map-view");
    if (map && map.classList.contains("active")) window.dispatchEvent(new HashChangeEvent("hashchange"));
  });

  window.Atlas = { showMap, fillAreaSelect, taxonomy };
})();
