const state = { page: 1, pageSize: 25, entityPage: 1, entityPageSize: 25, topicPage: 1, topicPageSize: 20, venues: [], years: [], topics: [], collections: [], currentCollectionId: null, selection: new Set(), planSelection: new Set(), detailTarget: null, detailPage: 1, detailPageSize: 25 };
let collectionRequest = 0;
let downloadPlanRequest = 0;
let planCollectionId = null;
let planEntityIds = new Set();
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const formatNumber = (value) => new Intl.NumberFormat("zh-CN").format(value ?? 0);
const formatBytes = (bytes) => { if (!bytes) return "—"; const mb = bytes / (1024 * 1024); return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`; };
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));
// Venue wall palette (stably mapped by topic_overview family order)
const FAMILY_PALETTE = ["#5470c6", "#3ba272", "#fac858", "#ee6666", "#73c0de", "#f28c4e", "#9a60b4", "#2f9e9f", "#ea7ccc", "#b8813f"];
const UNCLASSIFIED_COLOR = "#9aa3ab";

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}

function postJson(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function deleteJson(path, body) {
  return api(path, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

function showError(error) {
  const banner = $("#error-banner");
  banner.textContent = error.message || String(error);
  banner.classList.remove("hidden");
}

function statCard(label, value, note) {
  const display = typeof value === "number" ? formatNumber(value) : escapeHtml(value ?? "—");
  return `<article class="stat-card"><span class="label">${label}</span><strong>${display}</strong><small>${note}</small></article>`;
}

function barRows(items, labelKey, valueKey, maxItems = items.length, rate = false) {
  const visible = items.slice(0, maxItems);
  const maximum = Math.max(...visible.map((item) => Number(item[valueKey])), 1);
  return visible.map((item) => {
    const value = Number(item[valueKey]);
    const width = rate ? value : (value / maximum) * 100;
    const display = rate ? `${value.toFixed(1)}%` : formatNumber(value);
    return `<div class="bar-row"><span class="bar-label" title="${escapeHtml(item[labelKey])}">${escapeHtml(item[labelKey])}</span><div class="bar-track"><div class="bar-fill" style="width:${Math.max(width, 0.8)}%"></div></div><span class="bar-value">${display}</span></div>`;
  }).join("");
}

async function loadOverview() {
  const [summary, venues, years] = await Promise.all([api("/api/summary"), api("/api/venues"), api("/api/years")]);
  state.venues = venues.items;
  state.years = years.items;
  const releaseId = summary.release.release_id || "";
  $("#release-id").textContent = releaseId.includes("-") ? `catalog ${releaseId.split("-").pop().slice(0, 8)}` : releaseId;
  $("#release-id").title = releaseId;
  $("#summary-cards").classList.remove("skeleton-grid");
  $("#summary-cards").innerHTML = [
    statCard("论文清单记录", summary.record_count, `${formatNumber(summary.abstract_count)} 条已有摘要`),
    statCard("统一论文实体", summary.entity_count, `${formatNumber(summary.merged_entity_count)} 个已归并实体`),
    statCard("会议 / 期刊", summary.venue_count, `${summary.conference_count} 个会议 · ${summary.journal_count} 个期刊`),
    statCard("年度清单", summary.edition_count, `${summary.min_year}—${summary.max_year}`),
  ].join("");
  $("#year-chart").innerHTML = barRows(years.items.map((x) => ({ ...x, label: String(x.year) })), "label", "record_count");
  const errors = summary.issue_counts.error || 0;
  $("#release-status").textContent = errors ? `${errors} 个阻塞问题` : "构建通过";
  $("#release-status").className = `badge ${errors ? "error" : "success"}`;
  $("#release-details").innerHTML = [
    ["构建时间", new Date(summary.release.created_at).toLocaleString("zh-CN")],
    ["源 CSV", `${summary.release.source_file_count} 个文件`],
    ["数据库完整性", summary.release.status === "ready" ? "ready" : summary.release.status],
    ["实体归并", `${formatNumber(summary.linked_record_count)} 条记录已建立关系`],
    ["清单状态", `${formatNumber(summary.final_count)} 最终 · ${formatNumber(summary.rolling_count)} 滚动`],
  ].map(([label, value]) => `<div class="detail"><span>${label}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  populateFilters();
  loadInsightHomeBlocks().catch(showError);
}

// ================= Data Insights 2.0 · Two home blocks =================

async function loadInsightHomeBlocks() {
  const treeEl = $("#topic-tree");
  const wallEl = $("#venue-wall");
  if (!treeEl || !wallEl) return;
  if (treeEl.children.length && wallEl.children.length) return;
  const [overview, venues] = await Promise.all([api("/api/topics"), api("/api/venue-overview")]);
  if (!treeEl.children.length) renderTopicTree(overview, treeEl, $("#topic-tree-meta"));
  if (!wallEl.children.length) renderVenueWall(venues, overview, wallEl, $("#venue-wall-meta"));
}

function renderTopicTree(overview, container, meta) {
  const classified = overview.classified_entity_count || 1;
  const html = (overview.topics || []).map((family) => {
    const rateOfAll = Number(family.coverage_rate || 0);
    const rateOfClass = +(100 * family.entity_count / classified).toFixed(1);
    const children = family.subtopics || [];
    const subHtml = children.map((child) => {
      if (child.subtopics && child.subtopics.length) {
        const chips = child.subtopics.map(ttChip).join("");
        return `<div class="tt-branch"><div class="tt-subhead"><span>${escapeHtml(child.name)}</span><small>${escapeHtml(child.topic_id)}</small><strong>${formatNumber(child.entity_count)}</strong></div><div class="tt-chips">${chips}</div></div>`;
      }
      return `<div class="tt-branch"><div class="tt-chips">${ttChip(child)}</div></div>`;
    }).join("");
    return `<div class="tt-family">
      <div class="tt-row" role="button" tabindex="0" aria-expanded="true">
        <span class="tt-name">${escapeHtml(family.name)}<small>${escapeHtml(family.topic_id)} · ${escapeHtml(family.description || "")}</small></span>
        <span class="tt-count" title="primary 实体数">${formatNumber(family.entity_count)}</span>
        <span class="tt-pct">全库 ${rateOfAll}% · 已分类 ${rateOfClass}%</span>
        <span class="tt-bar" title="占已分类实体比例"><b style="width:${Math.min(100, Math.max(rateOfClass, 0.4))}%"></b></span>
        <button class="tt-drill" data-drill="${escapeHtml(family.topic_id)}">构成论文 →</button>
        <span class="tt-caret">▾</span>
      </div>
      <div class="tt-sub">${subHtml || '<div class="muted">该族暂无叶级主题</div>'}</div>
    </div>`;
  }).join("");
  container.innerHTML = html || '<div class="empty">暂无主题数据</div>';
  if (meta) {
    meta.textContent = `已分类 ${formatNumber(overview.classified_entity_count)}（${overview.classified_rate}%）· 多标签率 ${overview.multi_label_rate}% · ${overview.run ? overview.run.config_version : "未运行"}`;
  }
  if (!container.dataset.bound) {
    container.dataset.bound = "1";
    container.addEventListener("click", (event) => {
      const drill = event.target.closest(".tt-drill");
      if (drill) { event.stopPropagation(); goTopicDetail(drill.dataset.drill); return; }
      const row = event.target.closest(".tt-row");
      if (!row) return;
      const sub = row.parentElement.querySelector(".tt-sub");
      if (sub) { sub.hidden = !sub.hidden; row.classList.toggle("collapsed", sub.hidden); row.setAttribute("aria-expanded", String(!sub.hidden)); }
    });
  }
}

function ttChip(leaf) {
  return `<span class="tt-chip" title="${escapeHtml(leaf.description || "")}">${escapeHtml(leaf.name)} <b>${formatNumber(leaf.entity_count)}</b></span>`;
}

// Jump to the "Direction trends" page and set this family as the filter
function goTopicFamily(topicId) {
  const ensure = state.topics.length
    ? Promise.resolve()
    : (state.topicsPromise || (state.topicsPromise = loadTopics().catch((error) => { state.topicsPromise = null; throw error; })));
  ensure.then(() => {
    activateSubTab("insights-view", "topics");
    const select = $("#topic-filters select[name=topic_id]");
    if (select && [...select.options].some((option) => option.value === topicId)) select.value = topicId;
    loadTopicPapers().catch(showError);
  }).catch(showError);
}

// ================= Data Insights 2.0 · Category/venue detail page =================

function goTopicDetail(topicId) {
  state.detailTarget = { kind: "topic", id: topicId };
  state.detailPage = 1;
  switchView("insights");
  activateSubTab("insights-view", "details");
  const url = new URL(window.location.href);
  url.searchParams.set("topic", topicId);
  url.searchParams.delete("venue");
  history.replaceState({}, "", url.toString());
}

function goVenueDetail(venue) {
  state.detailTarget = { kind: "venue", id: venue };
  state.detailPage = 1;
  switchView("insights");
  activateSubTab("insights-view", "details");
  const url = new URL(window.location.href);
  url.searchParams.set("venue", venue);
  url.searchParams.delete("topic");
  history.replaceState({}, "", url.toString());
}

function clearDetailTarget() {
  state.detailTarget = null;
  state.detailPage = 1;
  const url = new URL(window.location.href);
  url.searchParams.delete("topic");
  url.searchParams.delete("venue");
  history.replaceState({}, "", url.toString());
}

async function renderCurrentDetail() {
  if (!state.detailTarget) {
    $("#detail-empty").classList.remove("hidden");
    $("#detail-content").classList.add("hidden");
    return;
  }
  $("#detail-empty").classList.add("hidden");
  $("#detail-content").classList.remove("hidden");
  try {
    if (state.detailTarget.kind === "topic") {
      const data = await api(`/api/topic-detail?topic_id=${encodeURIComponent(state.detailTarget.id)}`);
      renderTopicDetail(data);
      await loadDetailPapers();
    } else {
      const data = await api(`/api/venue-detail?venue=${encodeURIComponent(state.detailTarget.id)}`);
      renderVenueDetail(data);
      await loadDetailPapers();
    }
  } catch (e) {
    showError(e);
  }
}

function topicFamilyColor(topicId) {
  const order = (state.topics || []).map((item) => item.topic_id);
  const index = order.indexOf(topicId);
  return index >= 0 ? FAMILY_PALETTE[index % FAMILY_PALETTE.length] : UNCLASSIFIED_COLOR;
}

function renderTopicDetail(d) {
  const t = d.topic;
  const m = d.metrics;
  $("#detail-eyebrow").textContent = `TOPIC · ${(t.level || "—").toUpperCase()}`;
  $("#detail-title").textContent = `${t.name}（${t.topic_id}）`;
  $("#detail-note").textContent = t.description || "";
  $("#detail-meta").textContent = `分类精度 review_progress 见评估页 · ${t.topic_id}`;
  $("#detail-stats").innerHTML = [
    ["primary 实体", m.entity_count],
    ["primary 平均分", m.average_score ?? "—"],
    ["总 assignment", m.assignment_count],
    ["extra 实体", m.extra_count],
    ["涉及 venue", m.venue_count],
    ["年份跨度", m.min_year && m.max_year ? `${m.min_year}—${m.max_year}` : "—"],
    ["final 记录", m.final_records],
    ["rolling 记录", m.rolling_records],
    ["覆盖", `${m.coverage_rate}%`],
  ].map(([label, value]) => `<div class="detail-stat"><span class="v">${typeof value === "number" ? formatNumber(value) : escapeHtml(String(value))}</span><span class="l">${escapeHtml(label)}</span></div>`).join("");

  // Main word cloud: subtopics (leaf topics within a family)
  const subtopics = d.subtopics || [];
  const subEl = $("#detail-subtopics");
  const subEmpty = $("#detail-subtopics-empty");
  if (subtopics.length === 0) {
    subEl.innerHTML = ""; subEmpty.hidden = false;
  } else {
    subEmpty.hidden = true;
    subEl.innerHTML = subtopics.map((s) => `<span class="cloud-chip" data-topic-id="${escapeHtml(s.topic_id)}" title="${escapeHtml(s.topic_id)} · ${s.share_rate}%"><span>${escapeHtml(s.name)}</span><span class="b">${formatNumber(s.entity_count)}</span><span class="muted">${s.share_rate}%</span></span>`).join("");
  }
  // Clicking a chip navigates to the topic detail (self-navigation back)
  subEl.querySelectorAll("[data-topic-id]").forEach((el) => el.addEventListener("click", () => goTopicDetail(el.dataset.topicId)));

  // Secondary word cloud: tech_tags
  const tags = d.tech_tags || [];
  const tagEl = $("#detail-techtags");
  const tagEmpty = $("#detail-techtags-empty");
  if (tags.length === 0) { tagEl.innerHTML = ""; tagEmpty.hidden = false; }
  else { tagEmpty.hidden = true; tagEl.innerHTML = tags.map((s) => `<span class="cloud-chip" title="tech_tag · ${s.share_rate}%"><span>${escapeHtml(s.tag)}</span><span class="b">${formatNumber(s.entity_count)}</span><span class="muted">${s.share_rate}%</span></span>`).join(""); }

  // Paradigm timeline
  renderTrend(d.trend, $("#detail-trend"), t.name);
  $("#detail-trend-hint").textContent = `（${t.name} primary 实体逐年）`;

  // Composition table: top_venues (rows are clickable to jump to venue detail)
  $("#detail-cross-title").textContent = "top 会议 / 期刊（点行跳 venue 详情）";
  const cross = d.top_venues || [];
  const crossEl = $("#detail-cross");
  if (cross.length === 0) crossEl.innerHTML = '<div class="muted">暂无 venue 分布</div>';
  else crossEl.innerHTML = `<div class="h">venue</div><div class="h">entity</div><div class="h">share</div><div class="h">records</div>` + cross.map((v) => `<div class="row" data-venue="${escapeHtml(v.venue)}"><span><a href="#" data-venue-link="${escapeHtml(v.venue)}">${escapeHtml(v.venue)}</a></span><span class="num">${formatNumber(v.entity_count)}</span><span class="share">${v.share_rate}%</span><span class="muted">${formatNumber(v.record_count)}</span></div>`).join("");
  crossEl.querySelectorAll("[data-venue-link]").forEach((el) => el.addEventListener("click", (event) => { event.preventDefault(); goVenueDetail(el.dataset.venueLink); }));
}

function renderVenueDetail(d) {
  const v = d.venue;
  const m = d.metrics;
  $("#detail-eyebrow").textContent = `VENUE · ${(v.venue_type || "—").toUpperCase()}`;
  $("#detail-title").textContent = v.venue;
  $("#detail-note").textContent = `${m.record_count} 条清单记录 · ${m.final_count} final / ${m.rolling_count} rolling`;
  $("#detail-meta").textContent = `${v.venue_type === "journal" ? "期刊" : "会议"} · ${m.year_count} 年跨度`;
  $("#detail-stats").innerHTML = [
    ["实体", m.entity_count],
    ["清单记录", m.record_count],
    ["年份跨度", m.min_year && m.max_year ? `${m.min_year}—${m.max_year}` : "—"],
    ["final", m.final_count],
    ["rolling", m.rolling_count],
  ].map(([label, value]) => `<div class="detail-stat"><span class="v">${typeof value === "number" ? formatNumber(value) : escapeHtml(String(value))}</span><span class="l">${escapeHtml(label)}</span></div>`).join("");

  // Main word cloud: topic_mix (11 buckets: 10 families + unclassified)
  const mix = d.topic_mix || [];
  const subEl = $("#detail-subtopics");
  const subEmpty = $("#detail-subtopics-empty");
  $("#detail-subtitle-hint").textContent = "（族根主题构成）";
  if (mix.length === 0) { subEl.innerHTML = ""; subEmpty.hidden = false; }
  else {
    subEmpty.hidden = true;
    subEl.innerHTML = mix.map((b) => {
      const cls = b.level === "unclassified" ? "is-unclassified" : (b.level === "family" ? "is-family" : "");
      const dataAttr = b.level === "family" ? `data-topic-id="${escapeHtml(b.topic_id)}"` : "";
      return `<span class="cloud-chip ${cls}" ${dataAttr} title="${escapeHtml(b.name)} · ${b.share_rate}%"><span>${escapeHtml(b.name)}</span><span class="b">${formatNumber(b.entity_count)}</span><span class="muted">${b.share_rate}%</span></span>`;
    }).join("");
    subEl.querySelectorAll("[data-topic-id]").forEach((el) => el.addEventListener("click", () => goTopicDetail(el.dataset.topicId)));
  }

  // Secondary word cloud: tech_tags
  const tags = d.tech_tags || [];
  const tagEl = $("#detail-techtags");
  const tagEmpty = $("#detail-techtags-empty");
  if (tags.length === 0) { tagEl.innerHTML = ""; tagEmpty.hidden = false; }
  else { tagEmpty.hidden = true; tagEl.innerHTML = tags.map((s) => `<span class="cloud-chip" title="tech_tag · ${s.share_rate}%"><span>${escapeHtml(s.tag)}</span><span class="b">${formatNumber(s.entity_count)}</span><span class="muted">${s.share_rate}%</span></span>`).join(""); }

  // Paradigm timeline
  renderTrend(d.trend, $("#detail-trend"), v.venue);
  $("#detail-trend-hint").textContent = `（${v.venue} 实体逐年）`;

  // Composition table: top_topics
  $("#detail-cross-title").textContent = "top 主题（点行跳 topic 详情）";
  const cross = d.top_topics || [];
  const crossEl = $("#detail-cross");
  if (cross.length === 0) crossEl.innerHTML = '<div class="muted">暂无主题分布</div>';
  else crossEl.innerHTML = `<div class="h">topic</div><div class="h">entity</div><div class="h">share</div><div class="h"></div>` + cross.map((s) => `<div class="row" data-topic-link="${escapeHtml(s.topic_id)}"><span><a href="#" data-topic-link="${escapeHtml(s.topic_id)}">${escapeHtml(s.name)}（${escapeHtml(s.topic_id)}）</a></span><span class="num">${formatNumber(s.entity_count)}</span><span class="share">${s.share_rate}%</span><span class="muted"></span></div>`).join("");
  crossEl.querySelectorAll("[data-topic-link]").forEach((el) => el.addEventListener("click", (event) => { event.preventDefault(); goTopicDetail(el.dataset.topicLink); }));
}

function renderTrend(trend, container, name) {
  const years = (trend && trend.years) || [];
  if (years.length === 0) { container.innerHTML = '<div class="muted">暂无趋势数据</div>'; return; }
  const maximum = Math.max(...years.map((y) => Number(y.total_entities) || 0), 1);
  container.innerHTML = years.map((y) => {
    const w = Math.max(2, Math.round(100 * Number(y.total_entities || 0) / maximum));
    const finalW = Math.round(w * Number(y.final_records || 0) / Math.max(Number(y.total_entities || 0), 1));
    return `<div class="trend-row"><div class="trend-num">${y.year}</div><div class="trend-bar final"><b style="width:${finalW}%"></b><b style="width:${w - finalW}%; background:#f0a75c"></b></div><div class="trend-num">${formatNumber(y.total_entities || 0)} 实体 / ${formatNumber(y.total_records || 0)} 记录</div></div>`;
  }).join("");
}

async function loadDetailPapers() {
  if (!state.detailTarget) return;
  const tableEl = $("#detail-papers-table");
  const pagerEl = $("#detail-papers-pager");
  const metaEl = $("#detail-papers-meta");
  let payload;
  if (state.detailTarget.kind === "topic") {
    payload = await api(`/api/topic-papers?topic_id=${encodeURIComponent(state.detailTarget.id)}&page=${state.detailPage}&page_size=${state.detailPageSize}`);
  } else {
    payload = await api(`/api/venue-papers?venue=${encodeURIComponent(state.detailTarget.id)}&page=${state.detailPage}&page_size=${state.detailPageSize}`);
  }
  const total = payload.total;
  metaEl.textContent = `共 ${formatNumber(total)} 条 · 第 ${payload.page} / ${payload.total_pages || 1} 页`;
  if (payload.items.length === 0) {
    tableEl.innerHTML = '<div class="muted">暂无论文</div>';
    pagerEl.classList.add("hidden");
    return;
  }
  const header = state.detailTarget.kind === "topic"
    ? `<tr><th>title</th><th>score</th><th>year</th><th>venues</th></tr>`
    : `<tr><th>title</th><th>year</th><th>list_status</th><th>entity_id</th></tr>`;
  const rows = payload.items.map((p) => {
    if (state.detailTarget.kind === "topic") {
      const venues = (p.appearances || []).map((a) => `${escapeHtml(a.venue)} ${a.year}`).slice(0, 3).join("、");
      return `<tr><td>${escapeHtml(p.title || "—")}</td><td>${(p.score ?? "—").toFixed ? Number(p.score).toFixed(2) : "—"}</td><td>${p.first_year ?? "—"}</td><td class="muted">${venues}</td></tr>`;
    } else {
      return `<tr><td>${escapeHtml(p.title || "—")}</td><td>${p.year ?? "—"}</td><td>${escapeHtml(p.list_status || "—")}</td><td class="muted">${escapeHtml((p.entity_id || "—").slice(0, 18))}</td></tr>`;
    }
  }).join("");
  tableEl.innerHTML = `<table>${header}${rows}</table>`;
  pagerEl.classList.toggle("hidden", payload.total_pages <= 1);
  $("#detail-page-info").textContent = `${payload.page} / ${payload.total_pages || 1}`;
}

// ================= Two-way navigation entries =================

function bindDetailCrossLinks() {
  // Detail page paper pagination
  const prev = $("#detail-prev-page");
  const next = $("#detail-next-page");
  if (prev && !prev.dataset.bound) {
    prev.dataset.bound = "1";
    prev.addEventListener("click", () => { if (state.detailPage > 1) { state.detailPage -= 1; loadDetailPapers().catch(showError); } });
  }
  if (next && !next.dataset.bound) {
    next.dataset.bound = "1";
    next.addEventListener("click", () => { state.detailPage += 1; loadDetailPapers().catch(showError); });
  }
  // Detail page empty-state return link
  const empty = $("#detail-empty");
  if (empty && !empty.dataset.bound) {
    empty.dataset.bound = "1";
    const back = document.createElement("button");
    back.className = "secondary-button";
    back.textContent = "← 返回数据洞察概览";
    back.addEventListener("click", () => { clearDetailTarget(); switchView("insights"); activateSubTab("insights-view", "overview"); });
    empty.appendChild(back);
  }
  // Detail page top return link
  const meta = $("#detail-meta");
  if (meta && !meta.dataset.boundBack) {
    meta.dataset.boundBack = "1";
    meta.style.cursor = "pointer";
    meta.addEventListener("click", () => { clearDetailTarget(); switchView("insights"); activateSubTab("insights-view", "overview"); });
    meta.title = "点击返回概览";
  }
}

function renderVenueWall(payload, overview, container, meta) {
  const order = (overview.topics || []).map((item) => item.topic_id);
  const colorOf = (topicId) => {
    const index = order.indexOf(topicId);
    return index >= 0 ? FAMILY_PALETTE[index % FAMILY_PALETTE.length] : "#8d9aa6";
  };
  const badgeFor = (venue) => venue.venue_type === "journal" ? `<span class="badge warning">期刊</span>` : `<span class="badge success">会议</span>`;
  const cards = (payload.items || []).map((venue) => {
    const segs = venue.topic_mix.map((bucket) => {
      const color = bucket.name === "未分类" ? UNCLASSIFIED_COLOR : colorOf(bucket.topic_id);
      return `<i class="ven-seg" style="width:${bucket.share_rate}%;background:${color}" title="${escapeHtml(bucket.name)} ${bucket.share_rate}% · ${formatNumber(bucket.entity_count)} 实体"></i>`;
    }).join("");
    const legendTop = venue.topic_mix.slice(0, 4).map((bucket) => {
      const color = bucket.name === "未分类" ? UNCLASSIFIED_COLOR : colorOf(bucket.topic_id);
      return `<span class="ven-legend-item"><i style="background:${color}"></i>${escapeHtml(bucket.name)} ${bucket.share_rate}%</span>`;
    }).join("");
    const extra = venue.topic_mix.length > 4 ? `<span class="ven-legend-more">+${venue.topic_mix.length - 4} 类</span>` : "";
    return `<article class="ven-card">
      <div class="ven-head">${badgeFor(venue)}<h3 title="${escapeHtml(venue.venue)}"><a href="#" data-venue-go="${escapeHtml(venue.venue)}">${escapeHtml(venue.venue)}</a></h3><span class="ven-years">${venue.min_year}—${venue.max_year}</span></div>
      <div class="ven-stats"><strong>${formatNumber(venue.entity_count)}</strong> 研究实体<span class="dot">·</span><strong>${formatNumber(venue.record_count)}</strong> 清单记录<span class="dot">·</span><span>final ${formatNumber(venue.final_count)} / rolling ${formatNumber(venue.rolling_count)}</span></div>
      <div class="ven-mix" title="收录实体的主方向构成（点击下方分类可查看占比）">${segs || '<i class="ven-seg" style="width:100%;background:#edf1f2"></i>'}</div>
      <div class="ven-legend">${legendTop}${extra}</div>
    </article>`;
  }).join("");
  container.innerHTML = cards || '<div class="empty">暂无 venue 数据</div>';
  if (meta) {
    const conferences = payload.items.filter((item) => item.venue_type === "conference").length;
    const journals = payload.items.length - conferences;
    meta.textContent = `${payload.total} 个 venue · 会议 ${conferences} · 期刊 ${journals}`;
  }
  // Clicking the h3 on a venue card navigates to its detail
  container.querySelectorAll("[data-venue-go]").forEach((el) => el.addEventListener("click", (event) => { event.preventDefault(); goVenueDetail(el.dataset.venueGo); }));
}

function entityQuery() {
  const form = new FormData($("#entity-filters"));
  const params = new URLSearchParams({ page: state.entityPage, page_size: state.entityPageSize });
  for (const [key, value] of form.entries()) if (String(value).trim()) params.set(key, String(value).trim());
  if (!form.get("entity_status")) params.set("entity_status", "all");
  return params;
}

function renderMembers(members) {
  return `<details class="entity-details"><summary>查看 ${members.length} 条原始记录</summary><div class="member-list">${members.map((member) => `<div class="member-item"><div><strong>${escapeHtml(member.venue)} · ${member.year}</strong>${member.is_canonical ? `<span class="badge success">主记录</span>` : ""}<p>${escapeHtml(member.title)}</p></div><div><span class="badge ${member.list_status === "rolling" ? "warning" : "success"}">${member.list_status}</span><small>${escapeHtml(member.match_method)}: ${escapeHtml(member.evidence_value || "单记录")}</small></div></div>`).join("")}</div></details>`;
}

async function loadEntities() {
  const [quality, data, candidates] = await Promise.all([
    api("/api/entity-quality"),
    api(`/api/entities?${entityQuery()}`),
    api("/api/entity-candidates?page_size=100&review_status=pending"),
  ]);
  $("#entity-cards").innerHTML = [
    statCard("论文实体", quality.entity_count, "所有目录记录均已映射"),
    statCard("已归并实体", quality.merged_entity_count, "当前仅采用可审计强证据"),
    statCard("已关联记录", quality.linked_record_count, "原始记录不删除、不覆盖"),
    statCard("待人工复核", quality.pending_review_count, "不确定关系不自动归并"),
  ].join("");
  $("#entity-method-chart").innerHTML = barRows(quality.match_methods.map((item) => ({ ...item, label: item.match_method === "singleton" ? "单记录" : item.match_method.toUpperCase() })), "label", "record_count");
  $("#entity-count").textContent = `共 ${formatNumber(data.total)} 个实体`;
  $("#entity-page-info").textContent = `第 ${data.page} / ${Math.max(data.total_pages, 1)} 页`;
  $("#prev-entity-page").disabled = data.page <= 1;
  $("#next-entity-page").disabled = data.page >= data.total_pages;
  $("#entities-table").innerHTML = data.items.length ? `<table><thead><tr><th>论文实体</th><th>记录</th><th>Venue</th><th>年份</th><th>归并依据</th></tr></thead><tbody>${data.items.map((entity) => `<tr><td class="paper-title">${escapeHtml(entity.canonical_title)}<div class="muted entity-id">${escapeHtml(entity.entity_id)}</div>${renderMembers(entity.members)}</td><td>${entity.record_count}</td><td>${entity.venue_count}</td><td class="nowrap">${entity.first_year === entity.last_year ? entity.first_year : `${entity.first_year}—${entity.last_year}`}</td><td><span class="badge success">${escapeHtml(entity.members[0]?.match_method || "singleton")}</span><div class="muted evidence-value">${escapeHtml(entity.members[0]?.evidence_value || "独立记录")}</div></td></tr>`).join("")}</tbody></table>` : `<div class="empty">当前筛选条件下没有论文实体</div>`;

  $("#candidate-count").textContent = `共 ${formatNumber(candidates.total)} 组`;
  $("#candidate-table").innerHTML = candidates.items.length ? `<table><thead><tr><th>关系</th><th>记录 A</th><th>记录 B</th><th>为什么需要复核</th></tr></thead><tbody>${candidates.items.map((item) => `<tr><td><span class="badge ${item.confidence === "conflict" ? "error" : "warning"}">${escapeHtml(item.candidate_method)}</span><div class="muted evidence-value">${escapeHtml(item.evidence_value)}</div></td><td class="candidate-paper"><strong>${escapeHtml(item.left_title)}</strong><span>${escapeHtml(item.left_venue)} · ${item.left_year}</span></td><td class="candidate-paper"><strong>${escapeHtml(item.right_title)}</strong><span>${escapeHtml(item.right_venue)} · ${item.right_year}</span></td><td>${escapeHtml(item.reason)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">当前没有待复核关系</div>`;
}

function renderTopicCard(topic) {
  const subtopics = topic.subtopics.map((item) => `<span class="topic-chip">${escapeHtml(item.name)} <strong>${formatNumber(item.entity_count)}</strong></span>`).join("");
  const venues = topic.top_venues.map((item) => `<span>${escapeHtml(item.venue)} ${formatNumber(item.entity_count)}</span>`).join("");
  return `<article class="panel topic-card" title="${escapeHtml(topic.topic_id)}"><div class="topic-card-head"><div><h2>${escapeHtml(topic.name)}</h2></div><strong>${formatNumber(topic.entity_count)}</strong></div><p>${escapeHtml(topic.description)}</p><div class="topic-chips">${subtopics}</div><div class="topic-venues"><small>TOP VENUES</small>${venues}</div><button class="text-button topic-drilldown" data-topic="${escapeHtml(topic.topic_id)}">查看构成论文 →</button></article>`;
}

const bandLabel = (band) => ({ high: "高分", middle: "中分", boundary: "边界" })[band] || band;
const verdictLabel = (verdict) => ({ relevant: "相关", mention_only: "仅提及", wrong_signal: "错误信号", uncertain: "待定" })[verdict] || verdict;
const issueTypeLabel = (code) => ({
  cross_venue_doi: "跨 venue 重复 DOI",
  incomplete_abstract_provenance: "摘要来源信息不完整",
  orphan_abstract_provenance: "摘要来源信息多余",
  invalid_url: "URL 格式异常",
})[code] || code;

const errorLabel = (error) => ({
  non_llm_agent: "非 LLM Agent", incidental_mention: "偶然提及", incidental_method: "非核心方法",
  scope_too_broad: "范围过宽", acronym_collision: "缩写冲突", incidental_benchmark: "仅作评测",
  modality_collision: "模态冲突",
})[error] || error;

function precisionCell(metric) {
  if (metric.preliminary_precision == null) return "—";
  const interval = metric.wilson_95 ? `${metric.wilson_95[0]}–${metric.wilson_95[1]}%` : "—";
  return `<strong>${metric.preliminary_precision}%</strong><div class="muted">95% CI ${interval}</div>`;
}

function renderEvaluation(data, sampleData) {
  const overall = data.overall;
  $("#evaluation-summary").innerHTML = [
    statCard("固定样本", overall.sampled_count, `3 个主题 · 每类 100 条`),
    statCard("已审阅", overall.reviewed_count, `${data.review_progress}% · 尚待 ${formatNumber(overall.pending_count)} 条`),
    statCard("初步精度", overall.preliminary_precision == null ? "—" : `${overall.preliminary_precision}%`,
      overall.wilson_95 ? `95% CI ${overall.wilson_95[0]}–${overall.wilson_95[1]}%` : "95% CI —"),
  ].join("");
  $("#evaluation-topic-table").innerHTML = `<table><thead><tr><th>主题</th><th>已审 / 样本</th><th>高 / 中 / 边界</th><th>总体</th></tr></thead><tbody>${data.topics.map((topic) => `<tr><td><strong>${escapeHtml(topic.topic_name)}</strong></td><td>${topic.reviewed_count} / ${topic.sampled_count}</td><td class="band-metrics">${topic.bands.map((band) => `<span>${bandLabel(band.score_band)} <strong>${band.preliminary_precision ?? "—"}%</strong></span>`).join("")}</td><td>${precisionCell(topic)}</td></tr>`).join("")}</tbody></table>`;
  $("#evaluation-errors").innerHTML = data.error_groups.length ? data.error_groups.map((item) => `<div class="error-group"><span>${escapeHtml(errorLabel(item.error_type))}</span><strong>${item.count}</strong></div>`).join("") : `<div class="empty">暂无已分组误差</div>`;
  const issues = sampleData.items.filter((item) => item.verdict === "wrong_signal" || item.verdict === "mention_only");
  $("#evaluation-samples").innerHTML = issues.length ? `<table><thead><tr><th>论文</th><th>主题 / 分层</th><th>判定</th><th>原因</th></tr></thead><tbody>${issues.map((item) => `<tr><td class="paper-title">${escapeHtml(item.title)}<div class="muted">${escapeHtml(item.appearances)}</div></td><td>${escapeHtml(item.topic_name)}<div class="muted">${bandLabel(item.score_band)} · ${Number(item.score).toFixed(1)}</div></td><td><span class="badge ${item.verdict === "wrong_signal" ? "error" : "warning"}">${verdictLabel(item.verdict)}</span><div class="muted">${escapeHtml(errorLabel(item.error_type))}</div></td><td>${escapeHtml(item.rationale)}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">当前未发现问题样本</div>`;
  $("#evaluation-note").textContent = `${data.metric_note} 评估版本：${data.evaluation.evaluation_version}；分类版本：${data.evaluation.classifier_version}。`;
}

// ---- Abstract list: expand / collapse all on the current page ----
// Each row is a <details class="abstract-details">; two fixed-wording buttons in the
// panel head show/hide by state so the bilingual (static-chrome) labels never need
// rewriting. Both buttons are hidden unless the current page actually has abstracts.
function syncAbstractToggle(group) {
  if (!group) return;
  const expand = group.querySelector('.abstract-action[data-action="expand"]');
  const collapse = group.querySelector('.abstract-action[data-action="collapse"]');
  if (!expand || !collapse) return;
  const details = group.querySelectorAll("details.abstract-details");
  if (!details.length) { expand.classList.add("hidden"); collapse.classList.add("hidden"); return; }
  let openCount = 0;
  details.forEach((d) => { if (d.open) openCount += 1; });
  expand.classList.toggle("hidden", openCount === details.length);
  collapse.classList.toggle("hidden", openCount === 0);
}

function afterAbstractListRender(id) {
  const wrap = $(id);
  const group = wrap && wrap.closest(".panel");
  if (group) syncAbstractToggle(group);
}

document.addEventListener("click", (event) => {
  const button = event.target.closest(".abstract-action");
  if (!button) return;
  const group = button.closest(".panel");
  if (!group) return;
  const open = button.dataset.action === "expand";
  group.querySelectorAll("details.abstract-details").forEach((details) => { details.open = open; });
});
document.addEventListener("toggle", (event) => {
  const node = event.target;
  if (node && node.classList && node.classList.contains("abstract-details")) {
    const group = node.closest(".panel");
    if (group) syncAbstractToggle(group);
  }
}, true);

function topicPaperQuery() {
  const form = new FormData($("#topic-filters"));
  const params = new URLSearchParams({ page: state.topicPage, page_size: state.topicPageSize });
  for (const [key, value] of form.entries()) if (String(value).trim()) params.set(key, String(value).trim());
  if (!params.get("topic_id") && state.topics.length) params.set("topic_id", state.topics[0].topic_id);
  return params;
}

async function loadTopicPapers() {
  if (!state.topics.length) return;
  const data = await api(`/api/topic-papers?${topicPaperQuery()}`);
  const selected = state.topics.find((item) => item.topic_id === $("#topic-filters select[name=topic_id]").value);
  $("#topic-paper-title").textContent = selected ? `${selected.name}·构成论文` : "主题论文";
  $("#topic-paper-count").textContent = `共 ${formatNumber(data.total)} 篇`;
  $("#topic-page-info").textContent = `第 ${data.page} / ${Math.max(data.total_pages, 1)} 页`;
  $("#prev-topic-page").disabled = data.page <= 1;
  $("#next-topic-page").disabled = data.page >= data.total_pages;
  $("#topic-papers-table").innerHTML = data.items.length ? `<table><thead><tr><th><input type="checkbox" class="select-all-page" title="全选本页" /></th><th>论文实体</th><th>Venue / 年份</th><th>弱标签得分</th><th>命中依据</th></tr></thead><tbody>${data.items.map((paper) => `<tr><td><input type="checkbox" class="select-entity" data-entity="${escapeHtml(paper.entity_id)}" /></td><td class="paper-title">${paper.paper_url ? `<a href="${escapeHtml(paper.paper_url)}" target="_blank" rel="noreferrer">${escapeHtml(paper.title)}</a>` : escapeHtml(paper.title)}<div class="muted">${escapeHtml(paper.authors || "作者未知")}</div>${paper.abstract ? `<details class="abstract-details"><summary>查看摘要</summary><p>${escapeHtml(paper.abstract)}</p></details>` : ""}</td><td>${paper.appearances.map((item) => `<div class="appearance"><strong>${escapeHtml(item.venue)}</strong> · ${item.year}<span class="badge ${item.list_status === "rolling" ? "warning" : "success"}">${item.list_status}</span></div>`).join("")}</td><td><span class="topic-score">${Number(paper.score).toFixed(1)}</span></td><td class="evidence-list">${paper.evidence.map((item) => `<span><strong>${escapeHtml(item.signal)}</strong><small>${escapeHtml(item.field)} · +${Number(item.points).toFixed(1)}</small></span>`).join("")}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">当前筛选条件下没有主题论文</div>`;
  attachSelection();
  afterAbstractListRender("#topic-papers-table");
}

async function loadTopics() {
  const [overview, trends] = await Promise.all([api("/api/topics"), api("/api/topic-trends")]);
  state.topics = overview.topics;
  $("#topic-cards").innerHTML = overview.topics.map(renderTopicCard).join("");
  const trendMap = new Map(trends.items.map((item) => [`${item.year}:${item.topic_id}`, item]));
  $("#topic-trend-table").innerHTML = `<table class="trend-table"><thead><tr><th>年份</th><th>清单状态</th>${overview.topics.map((topic) => `<th>${escapeHtml(topic.name)}</th>`).join("")}</tr></thead><tbody>${trends.years.map((year) => `<tr><td><strong>${year.year}</strong></td><td><span class="badge ${year.rolling_records ? "warning" : "success"}">${year.rolling_records ? "rolling" : "final"}</span><div class="muted">${formatNumber(year.total_entities)} 个实体</div></td>${overview.topics.map((topic) => { const item = trendMap.get(`${year.year}:${topic.topic_id}`) || {entity_count:0,share_rate:0}; return `<td><strong>${formatNumber(item.entity_count)}</strong><div class="muted">${item.share_rate}%</div></td>`; }).join("")}</tr>`).join("")}</tbody></table>`;
  const topicSelect = $("#topic-filters select[name=topic_id]");
  topicSelect.innerHTML = overview.topics.map((topic) => `<option value="${escapeHtml(topic.topic_id)}">${escapeHtml(topic.name)}</option>`).join("");
  const yearSelect = $("#topic-filters select[name=year]");
  yearSelect.innerHTML = `<option value="">全部</option>${[...state.years].reverse().map((item) => `<option value="${item.year}">${item.year}</option>`).join("")}`;
  const venueSelect = $("#topic-filters select[name=venue]");
  venueSelect.innerHTML = `<option value="">全部</option>${state.venues.map((item) => `<option value="${escapeHtml(item.venue)}">${escapeHtml(item.venue)}</option>`).join("")}`;
  $$(".topic-drilldown").forEach((button) => button.addEventListener("click", () => { topicSelect.value = button.dataset.topic; state.topicPage = 1; loadTopicPapers().catch(showError); $("#topic-filters").scrollIntoView({ behavior: "smooth" }); }));
  await loadTopicPapers();
}

async function loadQuality() {
  const quality = await api("/api/quality");
  const errors = quality.issue_groups.filter((x) => x.severity === "error").reduce((sum, x) => sum + x.count, 0);
  const warnings = quality.issue_groups.filter((x) => x.severity === "warning").reduce((sum, x) => sum + x.count, 0);
  $("#quality-cards").innerHTML = [
    statCard("阻塞错误", errors, errors ? "需要修复后才能发布" : "当前构建无阻塞问题"),
    statCard("格式警告", warnings, "需要核对，但不阻塞浏览"),
    statCard("跨 Venue DOI", quality.cross_venue_doi_groups, "已知重复出版或需解释关系"),
  ].join("");
  $("#completeness-chart").innerHTML = barRows(quality.completeness, "field", "rate", quality.completeness.length, true);
  const sourceRows = (items) => items.map((item) => `<div class="source-item"><div><strong>${escapeHtml(item.source_tier)}</strong><div class="muted">${formatNumber(item.count)} 条记录</div></div><span class="badge success">${item.rate}%</span></div>`).join("");
  $("#source-tier-list").innerHTML = `<h3>目录元数据</h3>${sourceRows(quality.source_tiers)}<h3>摘要元数据</h3>${sourceRows(quality.abstract_source_tiers)}`;
  $("#issue-table").innerHTML = quality.issue_groups.length ? `<table><thead><tr><th>等级</th><th>类型</th><th>数量</th><th>解释</th></tr></thead><tbody>${quality.issue_groups.map((item) => `<tr><td><span class="badge ${item.severity === "error" ? "error" : item.severity === "warning" ? "warning" : "success"}">${item.severity}</span></td><td>${escapeHtml(issueTypeLabel(item.issue_type))}</td><td>${formatNumber(item.count)}</td><td>${item.issue_type === "cross_venue_doi" ? "同一 DOI 出现在多个 venue，保留清单记录并单独识别" : "查看构建报告进一步核对"}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">当前没有质量问题</div>`;
  // Classification calibration lives on this tab too (moved here from the directions page).
  const [evaluation, evaluationSamples] = await Promise.all([
    api("/api/topic-evaluation"), api("/api/topic-evaluation-samples?page_size=25"),
  ]);
  renderEvaluation(evaluation, evaluationSamples);
}

function populateFilters() {
  const venueSelect = $("select[name=venue]");
  venueSelect.innerHTML = `<option value="">全部</option>${state.venues.map((x) => `<option value="${escapeHtml(x.venue)}">${escapeHtml(x.venue)}</option>`).join("")}`;
  const yearSelect = $("select[name=year]");
  yearSelect.innerHTML = `<option value="">全部</option>${[...state.years].reverse().map((x) => `<option value="${x.year}">${x.year}</option>`).join("")}`;
}

function currentQuery() {
  const form = new FormData($("#filters"));
  const params = new URLSearchParams({ page: state.page, page_size: state.pageSize });
  for (const [key, value] of form.entries()) if (String(value).trim()) params.set(key, String(value).trim());
  return params;
}

async function loadPapers() {
  const data = await api(`/api/papers?${currentQuery()}`);
  $("#paper-count").textContent = `共 ${formatNumber(data.total)} 条`;
  $("#page-info").textContent = `第 ${data.page} / ${Math.max(data.total_pages, 1)} 页`;
  $("#prev-page").disabled = data.page <= 1;
  $("#next-page").disabled = data.page >= data.total_pages;
  $("#papers-table").innerHTML = data.items.length ? `<table><thead><tr><th><input type="checkbox" class="select-all-page" title="全选本页" /></th><th>论文</th><th>Venue</th><th>年份</th><th>状态</th><th>作者</th><th>标识</th></tr></thead><tbody>${data.items.map((paper) => `<tr><td><input type="checkbox" class="select-entity" data-entity="${escapeHtml(paper.entity_id)}" /></td><td class="paper-title">${paper.paper_url ? `<a href="${escapeHtml(paper.paper_url)}" target="_blank" rel="noreferrer">${escapeHtml(paper.title)}</a>` : escapeHtml(paper.title)}<div class="muted">${escapeHtml(paper.track)} · ${escapeHtml(paper.source_tier)}</div>${paper.abstract ? `<details class="abstract-details"><summary>查看摘要</summary><p>${escapeHtml(paper.abstract)}</p><small>来源：${paper.abstract_source_url ? `<a href="${escapeHtml(paper.abstract_source_url)}" target="_blank" rel="noreferrer">${escapeHtml(paper.abstract_source_name)}</a>` : escapeHtml(paper.abstract_source_name)}</small></details>` : `<div class="abstract-missing">暂未获得可追溯摘要</div>`}</td><td class="nowrap"><strong>${escapeHtml(paper.venue)}</strong><div class="muted">${paper.venue_type === "conference" ? "会议" : paper.venue_type === "preprint" ? "预印本" : "期刊"}</div></td><td>${paper.year}</td><td><span class="badge ${paper.list_status === "rolling" ? "warning" : "success"}">${paper.list_status}</span></td><td class="authors">${escapeHtml(paper.authors || "未知")}</td><td class="nowrap">${paper.doi ? `<div>DOI</div><div class="muted">${escapeHtml(paper.doi)}</div>` : paper.arxiv_id ? `<div>arXiv</div><div class="muted">${escapeHtml(paper.arxiv_id)}</div>` : `<span class="muted">—</span>`}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">当前筛选条件下没有论文记录</div>`;
  attachSelection();
  afterAbstractListRender("#papers-table");
}

// ---- Paper collections ----
function collectionSourceLabel(sourceType) {
  return { manual: "手动", filter: "筛选", topic: "主题", mixed: "混合" }[sourceType] || sourceType;
}

function statusLabel(status) {
  return { current: "当前", alias: "别名迁移", missing: "已失效" }[status] || status;
}

async function loadCollections() {
  const data = await api("/api/collections");
  state.collections = data.items;
  renderCollectionList();
  renderCollectionBar();
}

const myLibrary = {items: [], filter: "all", selected: new Set(), request: 0};

async function loadLocalLibrary() {
  const request = ++myLibrary.request;
  const data = await api("/api/my-library");
  if (request !== myLibrary.request) return;
  myLibrary.items = data.items || [];
  if (window.Research) await window.Research.sync();
  const known = new Set(myLibrary.items.map(item => item.entity_id));
  myLibrary.selected.forEach(id => { if (!known.has(id)) myLibrary.selected.delete(id); });
  renderMyPapers();
}

function readingState(item) {
  if (item.index_status === "indexed_current") return {key: "ready", label: "可全文问答"};
  if ([item.pdf_status, item.parse_status].some(s => ["pending", "downloading", "parsing"].includes(s))) return {key: "processing", label: "处理中"};
  if (!item.pdf_status && !item.parse_status && !item.index_status) return {key: "missing", label: "未获取全文"};
  return {key: "attention", label: "需要处理"};
}

function renderMyPapers() {
  const query = $("#my-paper-search").value.trim().toLowerCase();
  const status = $("#my-paper-status").value;
  const items = myLibrary.items.filter(item =>
    (myLibrary.filter === "all" || (myLibrary.filter === "unfiled" ? !item.collections?.length : item.collections?.some(c => c.collection_id === myLibrary.filter))) &&
    (!query || `${item.title} ${item.entity_id}`.toLowerCase().includes(query)) &&
    (status === "all" || readingState(item).key === status));
  $("#my-library-title").textContent = myLibrary.filter === "all" ? "全部论文" : myLibrary.filter === "unfiled" ? "未归类" : state.collections.find(c => c.collection_id === myLibrary.filter)?.name || "集合";
  $("#local-library").innerHTML = `<p class="muted">${items.length} 篇 · 收藏到集合不等于已获取全文</p><div class="my-paper-actions"><span>已选 ${myLibrary.selected.size} 篇</span><button id="my-add" class="primary-button" ${myLibrary.selected.size ? "" : "disabled"}>加入集合</button><button id="my-fetch" class="secondary-button" ${myLibrary.selected.size && !myLibrary.fetching ? "" : "disabled"}>${myLibrary.fetching ? "处理中…" : "获取全文"}</button><button id="my-ask" class="secondary-button">围绕所选提问</button><button id="my-compare" class="secondary-button">比较所选</button><button id="my-clear" class="text-button">清除选择</button></div>${items.length ? `<div class="table-wrap"><table class="my-papers-table"><thead><tr><th>选择</th><th>论文</th><th>阅读状态</th><th>所属集合</th></tr></thead><tbody>${items.map(item => `<tr><td><input class="local-paper-select" type="checkbox" value="${escapeHtml(item.entity_id)}" aria-label="选择 ${escapeHtml(item.title)}" ${myLibrary.selected.has(item.entity_id) ? "checked" : ""}/></td><td class="paper-title">${escapeHtml(item.title)}<div class="my-paper-links">${["success", "duplicate"].includes(item.pdf_status) ? `<a href="/api/downloads/${encodeURIComponent(item.entity_id)}/pdf" target="_blank" rel="noreferrer">打开 PDF</a>` : ""}${item.parse_status === "success" ? ` <a href="/api/parse/${encodeURIComponent(item.entity_id)}/md" target="_blank" rel="noreferrer">阅读解析文本</a>` : ""}</div><details><summary>处理详情</summary><div class="muted">${escapeHtml(item.entity_id)}<br>PDF：${escapeHtml(item.pdf_status || "未获取")} · 解析：${escapeHtml(item.parse_status || "未解析")}<br>索引：${escapeHtml(item.index_status || "未建立")} · chunk：${item.chunk_count || 0}<br>${escapeHtml(item.failure || "")}</div></details></td><td><span class="reading-state">${readingState(item).label}</span><select class="reading-progress" data-entity="${escapeHtml(item.entity_id)}" aria-label="阅读进度 ${escapeHtml(item.title)}">${[ ["unread","待读"],["reading","阅读中"],["read","已读"] ].map(([v,label]) => `<option value="${v}" ${(window.Research?.reading(item.entity_id) || "unread") === v ? "selected" : ""}>${label}</option>`).join("")}</select></td><td>${escapeHtml((item.collections || []).map(c => c.name).join("、") || "未归类")}</td></tr>`).join("")}</tbody></table></div>` : '<div class="empty">暂无论文。到公共目录选择论文加入集合，或在聊天中获取论文。</div>'}`;
  $$(".local-paper-select").forEach(box => box.addEventListener("change", () => {
    if (box.checked) myLibrary.selected.add(box.value); else myLibrary.selected.delete(box.value);
    renderMyPapers();
  }));
  $("#my-ask").onclick = () => window.Research?.startScoped().catch(showError);
  $("#my-compare").onclick = () => window.Research?.createComparison().catch(showError);
  $$(".reading-progress").forEach(select => select.onchange = () => window.Research?.saveReading(select.dataset.entity,select.value).catch(showError));
  $("#my-add").onclick = () => openPicker([...myLibrary.selected]).catch(showError);
  $("#my-clear").onclick = () => { myLibrary.selected.clear(); renderMyPapers(); };
  $("#my-fetch").onclick = () => fetchSelectedFulltext().catch(showError);
}

async function fetchSelectedFulltext() {
  const entity_ids = [...myLibrary.selected];
  if (!entity_ids.length || myLibrary.fetching) return;
  myLibrary.fetching = true;
  renderMyPapers();
  try {
    if (!await askConfirm(`获取所选 ${entity_ids.length} 篇论文的全文？将按配置下载、解析并建立索引，外部服务可能收费。`)) return;
    const conversation = await postJson("/api/chats", {title: "获取全文"});
    const task = await postJson(`/api/chats/${conversation.conversation_id}/ingest`, {entity_ids});
    $("#my-library-notice").textContent = "正在获取全文，可在聊天中查看任务结果…";
    await waitTask(task.task_id, "/api/downloads/status");
    $("#my-library-notice").textContent = "处理已结束，请检查逐篇阅读状态；详细结果已保存到“获取全文”会话。";
    await refreshMembershipViews();
  } finally { myLibrary.fetching = false; renderMyPapers(); }
}

async function refreshMembershipViews() {
  await loadCollections();
  await loadLocalLibrary();
  if (state.currentCollectionId && state.collections.some(c => c.collection_id === state.currentCollectionId)) {
    await openCollection(state.currentCollectionId);
  }
}

function renderCollectionBar() {
  const bar = $("#library-collections-bar");
  if (!bar) return;
  const items = state.collections;
  if (!items || !items.length) {
    bar.innerHTML = "";
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  bar.innerHTML = `<div class="bar-label">我的集合</div>
    <div class="bar-chips">${items.map((c) =>
      `<button class="collection-chip" data-cid="${c.collection_id}" title="${escapeHtml(c.description || c.name)}">${escapeHtml(c.name)}<span class="chip-count">${c.member_count}</span></button>`
    ).join("")}<button class="bar-action" id="bar-new-collection">＋ 新建</button></div>`;
  $$(".collection-chip").forEach((b) => b.addEventListener("click", () => {
    switchView("library");
    activateSubTab("library-view", "collections");
    openCollection(b.dataset.cid).catch(showError);
  }));
  const newBtn = $("#bar-new-collection");
  if (newBtn) newBtn.addEventListener("click", () => { activateSubTab("library-view", "collections"); $("#collection-create-form input[name=name]").focus(); });
}

function renderCollectionList() {
  const choices = [{collection_id:"all",name:"全部论文"},{collection_id:"unfiled",name:"未归类"},...state.collections];
  $("#collection-list").innerHTML = choices.map(c => `<button class="collection-nav ${myLibrary.filter === c.collection_id ? "active" : ""}" data-id="${escapeHtml(c.collection_id)}">${escapeHtml(c.name)}${c.member_count === undefined ? "" : `<span>${c.member_count}</span>`}</button>`).join("");
  $$(".collection-nav").forEach(button => button.onclick = () => {
    myLibrary.filter = button.dataset.id; myLibrary.selected.clear(); renderCollectionList(); renderMyPapers();
    if (!["all", "unfiled"].includes(myLibrary.filter)) openCollection(myLibrary.filter).catch(showError);
    else {
      collectionRequest += 1; downloadPlanRequest += 1; state.currentCollectionId = null;
      state.planSelection.clear(); planCollectionId = null; planEntityIds.clear(); syncSelectAll();
      $("#download-plan").innerHTML = "";
      $("#collection-detail-title").textContent = "集合详情";
      $("#collection-detail").innerHTML = "请选择集合查看设置"; $("#collection-actions").innerHTML = "";
    }
  });
}

async function openCollection(collectionId) {
  const request = ++collectionRequest;
  const data = await api(`/api/collections/${collectionId}`);
  if (request !== collectionRequest) return;
  myLibrary.filter = collectionId;
  renderCollectionList();
  renderMyPapers();
  renderCollectionDetail(data);
  loadDownloadPlan().catch(showError);
}

function renderCollectionDetail(data) {
  const collection = data.collection;
  if (state.currentCollectionId !== collection.collection_id) state.planSelection.clear();
  // Invalidate the previous plan while the new one is loading, including late responses.
  downloadPlanRequest += 1;
  planCollectionId = null;
  planEntityIds.clear();
  state.currentCollectionId = collection.collection_id;
  $("#collection-detail-title").textContent = collection.name;
  $("#download-plan").innerHTML = `<div class="empty">点击「生成下载计划」查看可下载范围与空间估算。</div>`;
  syncSelectAll();
  $("#pipeline-progress").classList.add("hidden");
  $("#pipeline-progress").innerHTML = "";
  $("#collection-actions").innerHTML = `<span class="muted">${formatNumber(collection.member_count)} 成员 · 当前 ${formatNumber(data.counts.current)} · 迁移 ${formatNumber(data.counts.alias)} · 失效 ${formatNumber(data.counts.missing)}</span><button class="secondary-button" data-export="csv">CSV</button><button class="secondary-button" data-export="json">JSON</button><button class="secondary-button" data-export="bibtex">BibTeX</button><button class="secondary-button" data-export="markdown">Markdown</button>`;
  $$("#collection-actions [data-export]").forEach((button) => button.addEventListener("click", () => exportCollection(collection.collection_id, button.dataset.export)));
  $("#collection-detail").innerHTML = data.items.length
    ? `<table><thead><tr><th>论文</th><th>Venue / 年份</th><th>状态</th><th>操作</th></tr></thead><tbody>${data.items.map((item) => `<tr><td class="paper-title">${item.status === "missing" ? `<span class="muted">已失效实体 ${escapeHtml(item.entity_id)}</span>` : (item.paper_url ? `<a href="${escapeHtml(item.paper_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>` : escapeHtml(item.title))}<div class="muted entity-id">${escapeHtml(item.entity_id)}</div>${item.authors ? `<div class="muted">${escapeHtml(item.authors)}</div>` : ""}</td><td>${(item.appearances || []).map((appearance) => `<div class="appearance"><strong>${escapeHtml(appearance.venue)}</strong> · ${appearance.year}<span class="badge ${appearance.list_status === "rolling" ? "warning" : "success"}">${appearance.list_status}</span></div>`).join("")}</td><td><span class="badge ${item.status === "missing" ? "error" : item.status === "alias" ? "warning" : "success"}">${statusLabel(item.status)}</span></td><td><button class="text-button collection-remove" data-entity="${escapeHtml(item.entity_id)}">移除</button></td></tr>`).join("")}</tbody></table>`
    : `<div class="empty">集合为空，去「论文目录」勾选论文加入。</div>`;
  $("#collection-detail").insertAdjacentHTML("afterbegin", `<form id="collection-settings" class="collection-create-form"><label>集合名称<input name="name" required value="${escapeHtml(collection.name)}"></label><label>智能分类规则<input name="description" value="${escapeHtml(collection.description || "")}" placeholder="描述哪些内容应该加入此集合"></label><button class="secondary-button">保存设置</button><button type="button" id="collection-delete" class="text-button">删除集合</button></form>`);
  $("#collection-delete").onclick = async () => {
    if (await askConfirm(`删除集合“${collection.name}”？此操作将移除该集合的成员关联。`)) deleteCollection(collection.collection_id).catch(showError);
  };
  $("#collection-settings").onsubmit = async event => {
    event.preventDefault();
    const button = event.target.querySelector("button");
    if (button.disabled) return;
    button.disabled = true;
    try {
      const form = new FormData(event.target);
      await api(`/api/collections/${collection.collection_id}`, {method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify({name:form.get("name"),description:form.get("description")})});
      await refreshMembershipViews();
      $("#my-library-notice").textContent = "集合设置已保存。";
    } catch (error) {showError(error);} finally {button.disabled = false;}
  };
  $$(".collection-remove").forEach((button) => button.addEventListener("click", () => removeMember(button.dataset.entity).catch(showError)));
}

async function removeMember(entityId) {
  await deleteJson(`/api/collections/${state.currentCollectionId}/members`, { entity_ids: [entityId] });
  await refreshMembershipViews();
}

async function deleteCollection(collectionId) {
  await api(`/api/collections/${collectionId}`, { method: "DELETE" });
  collectionRequest += 1;
  downloadPlanRequest += 1;
  state.currentCollectionId = null;
  state.planSelection.clear();
  planCollectionId = null;
  planEntityIds.clear();
  $("#download-plan").innerHTML = "";
  syncSelectAll();
  $("#collection-detail-title").textContent = "集合详情";
  $("#collection-actions").innerHTML = "";
  $("#collection-detail").innerHTML = `<div class="empty">从上方选择一个集合查看成员</div>`;
  myLibrary.filter = "all";
  await refreshMembershipViews();
}

function exportCollection(collectionId, format) {
  window.open(`/api/collections/${collectionId}/export?format=${format}`, "_blank");
}

function downloadSourceLabel(source) {
  return { arxiv: "arXiv 公开", open: "开放获取", restricted: "需订阅", unspecified: "来源未确认", none: "无公开入口" }[source] || source;
}

function downloadSourceBadge(source) {
  return { arxiv: "success", open: "success", restricted: "warning", unspecified: "warning", none: "error" }[source] || "success";
}

function downloadStatusLabel(status) {
  return { success: "已下载", duplicate: "已下载（重复）", failed: "失败", downloading: "下载中", pending: "待下载" }[status] || status;
}

function downloadStatusBadge(status) {
  return { success: "success", duplicate: "success", failed: "error", downloading: "warning", pending: "warning" }[status] || "success";
}

function parseStatusLabel(status) {
  return { success: "已解析", failed: "解析失败", parsing: "解析中", pending: "待解析" }[status] || status;
}

function parseStatusBadge(status) {
  return { success: "success", failed: "error", parsing: "warning", pending: "warning" }[status] || "success";
}

async function loadDownloadPlan() {
  if (!state.currentCollectionId) {
    showError(new Error("请先在列表中选择一个集合"));
    return;
  }
  const collectionId = state.currentCollectionId;
  const request = ++downloadPlanRequest;
  const data = await api(`/api/download-plan?collection_id=${collectionId}`);
  if (request !== downloadPlanRequest || collectionId !== state.currentCollectionId) return;
  renderDownloadPlan(data);
}

function renderDownloadPlan(data) {
  planCollectionId = state.currentCollectionId;
  planEntityIds = new Set(data.items.map((item) => item.entity_id));
  for (const id of state.planSelection) {
    if (!planEntityIds.has(id)) state.planSelection.delete(id);
  }
  const summary = data.summary;
  const restricted = summary.counts.restricted || 0;
  const unspecified = summary.counts.unspecified || 0;
  const downloaded = summary.downloaded_count || 0;
  const parsed = summary.parsed_count || 0;
  const ingested = summary.ingested_count || 0;
  $("#download-plan").innerHTML = `
    <div class="stat-grid">${[
      statCard("论文总数", summary.total, `${formatNumber(summary.downloadable)} 篇可下载`),
      statCard("已下载", downloaded, "PDF 已落盘"),
      statCard("已解析", parsed, "Markdown + 图片"),
      statCard("已入库", ingested, `需订阅 ${formatNumber(restricted)} · 未确认 ${formatNumber(unspecified)}`),
    ].join("")}</div>
    ${data.items.length ? `<table><thead><tr><th></th><th>论文</th><th>下载源</th><th>下载</th><th>解析</th><th>入库</th><th>打开</th></tr></thead><tbody>${data.items.map((item) => { const status = item.download_status || ""; const pstatus = item.parse_status || ""; const istatus = item.ingest_status || ""; const canOpen = status === "success" || status === "duplicate"; return `<tr><td><input type="checkbox" class="plan-select" data-entity="${escapeHtml(item.entity_id)}" /></td><td class="paper-title">${item.paper_url ? `<a href="${escapeHtml(item.paper_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>` : escapeHtml(item.title)}<div class="muted">${escapeHtml((item.venues || []).join(" · "))}</div></td><td><span class="badge ${downloadSourceBadge(item.source)}">${downloadSourceLabel(item.source)}</span>${item.arxiv_id ? `<div class="muted entity-id">${escapeHtml(item.arxiv_id)}</div>` : ""}</td><td>${status ? `<span class="badge ${downloadStatusBadge(status)}">${downloadStatusLabel(status)}</span>` : `<span class="muted">未下载</span>`}</td><td>${pstatus ? `<span class="badge ${parseStatusBadge(pstatus)}">${parseStatusLabel(pstatus)}</span>` : `<span class="muted">未解析</span>`}</td><td>${istatus ? `<span class="badge success">已入库</span>` : `<span class="muted">未入库</span>`}</td><td class="nowrap">${canOpen ? `<a class="text-button" href="/api/downloads/${escapeHtml(item.entity_id)}/pdf" target="_blank" rel="noreferrer">PDF</a>` : ""}${pstatus ? `<a class="text-button" href="/api/parse/${escapeHtml(item.entity_id)}/md" target="_blank" rel="noreferrer">MD</a>` : ""}${status ? `<button class="text-button single-delete" data-entity="${escapeHtml(item.entity_id)}" data-action="pdf">删PDF</button>` : ""}${pstatus ? `<button class="text-button single-delete" data-entity="${escapeHtml(item.entity_id)}" data-action="parse">删MD</button>` : ""}${istatus ? `<button class="text-button single-delete" data-entity="${escapeHtml(item.entity_id)}" data-action="ingest">删入库</button>` : ""}</td></tr>`; }).join("")}</tbody></table>` : `<div class="empty">集合为空，无可计划内容。</div>`}
  `;
  attachPlanSelection();
  $$(".single-delete").forEach((button) => button.addEventListener("click", () => singleDelete(button.dataset.entity, button.dataset.action).catch(showError)));
}

function attachPlanSelection() {
  const boxes = $$(".plan-select");
  boxes.forEach((checkbox) => {
    checkbox.checked = state.planSelection.has(checkbox.dataset.entity);
    checkbox.onchange = () => {
      if (checkbox.checked) state.planSelection.add(checkbox.dataset.entity);
      else state.planSelection.delete(checkbox.dataset.entity);
      syncSelectAll();
    };
  });
  syncSelectAll();
}

function syncSelectAll() {
  const all = $("#select-all-papers");
  const boxes = $$(".plan-select");
  if (all) {
    all.checked = boxes.length > 0 && boxes.every((cb) => cb.checked);
  }
}

async function createCollectionFromForm(event) {
  event.preventDefault();
  const button = event.target.querySelector("button");
  if (button.disabled) return;
  button.disabled = true;
  try {
    const form = new FormData(event.target);
    const created = await postJson("/api/collections", { name: form.get("name"), description: form.get("description"), source_type: "manual" });
    event.target.reset();
    await refreshMembershipViews();
    if (created.collection_id) await openCollection(created.collection_id);
  } finally { button.disabled = false; }
}

function attachSelection() {
  $$(".select-entity").forEach((checkbox) => {
    const entityId = checkbox.dataset.entity;
    checkbox.checked = state.selection.has(entityId);
    checkbox.onchange = () => {
      if (checkbox.checked) state.selection.add(entityId);
      else state.selection.delete(entityId);
      updateSelectionBar();
    };
  });
  $$(".select-all-page").forEach((header) => {
    const table = header.closest("table");
    header.onchange = () => {
      (table ? [...table.querySelectorAll(".select-entity")] : []).forEach((checkbox) => {
        checkbox.checked = header.checked;
        if (header.checked) state.selection.add(checkbox.dataset.entity);
        else state.selection.delete(checkbox.dataset.entity);
      });
      updateSelectionBar();
    };
  });
}

function updateSelectionBar() {
  const count = state.selection.size;
  $("#selection-bar").classList.toggle("hidden", count === 0 || !$("#papers-view").classList.contains("active") || !$("#library-view").classList.contains("active"));
  $("#selection-count").textContent = `已选 ${formatNumber(count)} 篇`;
}

async function selectAllPaperResults() {
  const params = new URLSearchParams();
  const form = new FormData($("#filters"));
  for (const [key, value] of form.entries()) if (String(value).trim()) params.set(key, String(value).trim());
  const data = await api(`/api/papers/entity-ids?${params}`);
  if (!data.total) {
    showError(new Error("当前筛选条件下没有匹配的论文"));
    return;
  }
  data.entity_ids.forEach((id) => state.selection.add(id));
  updateSelectionBar();
  attachSelection();
}

async function selectAllTopicResults() {
  const form = new FormData($("#topic-filters"));
  const params = new URLSearchParams();
  for (const [key, value] of form.entries()) if (String(value).trim()) params.set(key, String(value).trim());
  if (!params.get("topic_id") && state.topics.length) params.set("topic_id", state.topics[0].topic_id);
  if (!params.get("topic_id")) {
    showError(new Error("请先选择一个研究方向"));
    return;
  }
  const data = await api(`/api/topic-papers/entity-ids?${params}`);
  if (!data.total) {
    showError(new Error("当前筛选条件下没有匹配的主题论文"));
    return;
  }
  data.entity_ids.forEach((id) => state.selection.add(id));
  updateSelectionBar();
  attachSelection();
}

const pickerState = {ids: [], run: null, request: 0, timer: null, busy: false};
async function openPicker(ids = [...state.selection]) {
  if (pickerState.busy) return;
  pickerState.ids = [...ids]; pickerState.run = null;
  if (!pickerState.ids.length) return;
  await loadCollections();
  $("#picker-existing").innerHTML = `<option value="">创建新集合</option>` + state.collections.map(c => `<option value="${escapeHtml(c.collection_id)}">${escapeHtml(c.name)}</option>`).join("");
  $("#picker-new-name").value = "";
  $("#picker-count").textContent = `已选 ${pickerState.ids.length} 篇；仅添加集合归属，不获取全文。`;
  $("#picker-papers").textContent = pickerState.ids.join("、");
  $("#collection-picker").classList.remove("hidden");
  $("#picker-existing").onchange = schedulePickerDraft;
  $("#picker-new-name").oninput = schedulePickerDraft;
  schedulePickerDraft();
}
function schedulePickerDraft() {
  clearTimeout(pickerState.timer); pickerState.request += 1; pickerState.run = null;
  $("#picker-confirm").disabled = true;
  const target = $("#picker-existing").value;
  $("#picker-new-name").disabled = !!target;
  $("#picker-feedback").textContent = target || $("#picker-new-name").value.trim() ? "正在核对论文与集合…" : "请选择集合，或填写新集合名称。";
  if (target || $("#picker-new-name").value.trim()) pickerState.timer = setTimeout(() => preparePickerDraft().catch(error => {$("#picker-feedback").textContent = error.message;}), 250);
}
async function preparePickerDraft() {
  const request = pickerState.request;
  const target = $("#picker-existing").value;
  const name = $("#picker-new-name").value.trim();
  const plan = await postJson("/api/organization/plan", {operation:"assign", entity_ids:pickerState.ids, collection_ids:target ? [target] : [], target_name:target ? "" : name});
  if (request !== pickerState.request) return;
  pickerState.run = plan;
  $("#picker-papers").innerHTML = plan.suggestions.map(item => `<label class="picker-paper"><input type="checkbox" class="picker-paper-check" data-entity="${escapeHtml(item.entity_id)}" data-collection="${escapeHtml(item.collection_id)}" checked ${item.already_present ? "disabled" : ""}/> <span>${escapeHtml(item.title)} <small>${item.already_present ? "已存在" : "新增"}</small></span></label>`).join("");
  const similar = !target && state.collections.some(c => c.name.toLowerCase().includes(name.toLowerCase()) || name.toLowerCase().includes(c.name.toLowerCase()));
  $("#picker-feedback").textContent = `${plan.pending_collection ? "将创建集合“" + name + "”并加入。" : "目标：" + plan.rules.map(r => r.name).join("、") + "。"}新增 ${plan.summary.suggested_add} 篇，已存在 ${plan.summary.already_present} 篇。${similar ? "已有相似名称，请核对目标，或选择已有集合。" : ""}`;
  $("#picker-confirm").disabled = !plan.suggestions.length;
}
function closePicker() {
  if (pickerState.busy) return;
  pickerState.request += 1; clearTimeout(pickerState.timer);
  $("#collection-picker").classList.add("hidden");
}
async function confirmPicker() {
  if (pickerState.busy || !pickerState.run) return;
  const plan = pickerState.run;
  const selected = $$(".picker-paper-check:checked").map(box => ({entity_id:box.dataset.entity,collection_id:box.dataset.collection}));
  pickerState.busy = true; $("#picker-confirm").disabled = true;
  $("#picker-existing").disabled = true; $("#picker-new-name").disabled = true;
  try {
    const result = await postJson("/api/organization/apply", {run_id:plan.run_id, selected});
    state.selection.clear(); myLibrary.selected.clear(); updateSelectionBar();
    $$(".select-entity").forEach(box => {box.checked = false;});
    pickerState.busy = false; closePicker();
    $("#my-library-notice").textContent = `已加入 ${result.added || 0} 篇，已存在 ${result.already_present || 0} 篇；目标：${plan.rules.map(r => r.name).join("、")}。`;
    await refreshMembershipViews().catch(error => showError(new Error("收藏已保存，刷新失败：" + error.message)));
  } catch (error) { $("#picker-feedback").textContent = error.message; }
  finally { pickerState.busy = false; $("#picker-confirm").disabled = false; $("#picker-existing").disabled = false; $("#picker-new-name").disabled = !!$("#picker-existing").value; }
}

/* ===== Research Q&A Chat ===== */
const chatState = { current: null, pending: false, pendingTaskId: null, selected: new Set() };

const fmtTime = (iso) => {
  if (!iso) return "";
  try {
    return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(iso));
  } catch {
    return iso.slice(11, 16);
  }
};

// Chat-list timestamps: full YYYY-MM-DD HH:mm in Shanghai time, anchored to the
// conversation's creation time (created_at) so continuing a chat never moves it.
const fmtDateTimeFull = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 16);
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(d);
  const m = Object.fromEntries(parts.map((x) => [x.type, x.value]));
  return `${m.year}-${m.month}-${m.day} ${m.hour}:${m.minute}`;
};

const UNSAFE_URL = /^\s*(?:javascript|vbscript|data):/i;

function safeMarkup(html) {
  // marked v12 ships no sanitizer. escapeHtml() above already neutralizes raw HTML tags,
  // but markdown link/image syntax can still emit javascript:/data: hrefs/srcs (stored XSS
  // vector: a malicious page read by the agent can make the model emit such a link).
  // Strip the dangerous scheme so the crafted link degrades to inert text instead of JS.
  return html.replace(/<(a|img)\b[^>]*?(?:href|src)=(["'])([^"']*)\2/gi, (match, tag, quote, value) => {
    if (UNSAFE_URL.test(value)) return `<${tag}>`;
    return match;
  });
}

function mdToHtml(text) {
  const raw = escapeHtml(text);
  let html;
  try {
    html = marked.parse(raw, { breaks: true, gfm: true });
  } catch {
    html = `<p>${raw}</p>`;
  }
  html = safeMarkup(html);
  return html.replace(/\[(\d+)\]/g, (match, n) => `<sup class="cite" data-cite="${n}">[${n}]</sup>`);
}

function renderMathInElementIfPresent(el) {
  if (window.renderMathInElement) {
    renderMathInElement(el, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\(", right: "\\)", display: false },
        { left: "$", right: "$", display: false },
      ],
      throwOnError: false,
    });
  }
}

async function loadChats() {
  const [active, archived] = await Promise.all([
    api("/api/chats?status=active"),
    api("/api/chats?status=archived"),
  ]);
  renderChatList(active.items || [], archived.items || []);
}

function chatItemHtml(conv) {
  const activeCls = chatState.current && chatState.current.conversation_id === conv.conversation_id ? " chat-item-active" : "";
  const checked = chatState.selected.has(conv.conversation_id) ? " checked" : "";
  return `<div class="chat-item${activeCls}" data-cid="${conv.conversation_id}">
    <input type="checkbox" class="chat-select" data-cid="${conv.conversation_id}"${checked} />
    <div class="chat-item-body">
      <div class="chat-item-title">${escapeHtml(conv.title || "新对话")}</div>
      <div class="chat-item-time" title="${escapeHtml(conv.created_at || "")}">${escapeHtml(fmtDateTimeFull(conv.created_at))}</div>
    </div>
  </div>`;
}

function renderChatBatchBar() {
  const bar = $("#chat-batch-bar");
  if (!bar) return;
  const count = chatState.selected.size;
  if (count) {
    bar.classList.remove("hidden");
    $("#chat-batch-count").textContent = `已选 ${count} 个对话`;
  } else {
    bar.classList.add("hidden");
  }
}

function renderChatList(active, archived) {
  $("#chat-list").innerHTML = active.length
    ? active.map(chatItemHtml).join("")
    : `<div class="empty">暂无对话</div>`;
  const box = $("#chat-archived-box");
  if (archived.length) {
    box.classList.remove("hidden");
    $("#chat-archived-summary").textContent = `已归档（${archived.length}）`;
    $("#chat-archived-list").innerHTML = archived.map(chatItemHtml).join("");
  } else {
    box.classList.add("hidden");
  }
}

async function openChat(conversationId) {
  // Fix: allow switching conversations while the LLM is answering. Switching signals an intent
  // transfer — auto-stop the current answer to avoid the input lock caused by two conversations
  // sharing the pending state. stopChat removes the pending bubble and restores the input box.
  if (chatState.pending) await stopChat();
  const data = await api(`/api/chats/${conversationId}/messages`);
  chatState.current = data.conversation;
  $("#chat-header").classList.remove("hidden");
  $("#chat-title").textContent = data.conversation.title || "新对话";
  $("#chat-meta").textContent = data.messages.length ? `共 ${data.messages.length} 条消息` : "新对话";
  renderChatMessages(data.messages);
  window.Research?.showScope(conversationId).catch(showError);
  restoreIngestTaskState(conversationId).catch(showError);
  updateArchiveLabel();
  $("#chat-input").focus();
  loadChats().catch(showError);
}

async function restoreIngestTaskState(conversationId) {
  const data = await api(`/api/chats/${conversationId}/ingest-tasks`);
  if (!chatState.current || chatState.current.conversation_id !== conversationId) return;
  const task = (data.items || [])[0];
  if (!task) return;
  const box = document.createElement("div");
  box.className = "muted";
  box.textContent = task.status === "running"
    ? `已恢复入库任务状态：${task.task_id} 仍标记为运行中。若服务曾重启，请重新确认该任务。`
    : `最近入库任务：${task.status}（${task.finished_at || task.started_at}）。`;
  $("#chat-messages").prepend(box);
}

function updateArchiveLabel() {
  const button = $("#chat-archive-toggle");
  if (!button) return;
  button.textContent = chatState.current && chatState.current.status === "archived" ? "恢复" : "归档";
}

function evidenceItemHtml(citation, index) {
  const citationIndex = citation.citation_index || index + 1;
  const score = citation.score != null ? `<span class="chat-score">${Number(citation.score).toFixed(2)}</span>` : "";
  const link = citation.url
    ? ` <a class="evidence-link" href="${escapeHtml(citation.url)}" target="_blank" rel="noopener">↗ 原文</a>`
    : "";
  return `<div class="chat-evidence-item" data-evidence="${citationIndex}">
    <div class="chat-evidence-head"><span class="cite-badge">${citationIndex}</span>
      <strong>${escapeHtml(citation.title || citation.entity_id)}</strong>
      <span class="muted">${escapeHtml(citation.section || "")}</span>${link}${score}</div>
    <p>${escapeHtml(citation.text)}</p>
  </div>`;
}

function evidenceBoxHtml(chunks) {
  if (!chunks || !chunks.length) return "";
  const first = chunks[0] || {};
  const summary = `证据片段（${chunks.length}）· ${escapeHtml((first.title || first.entity_id || "").slice(0, 24))}…`;
  return `<details class="chat-evidence"><summary>${summary}</summary>
    <div class="chat-evidence-list">${chunks.map(evidenceItemHtml).join("")}</div></details>`;
}

function rawToolMarkup(content) {
  const value = String(content || "");
  return value.includes("DSML") && (value.includes("tool_calls") || value.includes("invoke"));
}

function messageHtml(message, lastUserContent) {
  if (message.role === "user") {
    const editBtn = chatState.pending
      ? ""
      : `<button class="chat-edit-btn" data-mid="${message.message_id}" title="编辑并重发">✎</button>`;
    return `<div class="chat-msg chat-msg-user"><div class="chat-bubble-user" data-mid="${message.message_id}">
      <span class="chat-msg-content">${escapeHtml(message.content)}</span>${editBtn}
      <div class="chat-meta-line"><span class="muted">${escapeHtml(fmtDateTimeFull(message.created_at))}</span></div></div></div>`;
  }
  if (message.error) {
    const query = escapeHtml(lastUserContent || "");
    return `<div class="chat-msg chat-msg-assistant"><div class="chat-bubble-assistant">
      <div class="chat-error">${escapeHtml(message.error)}</div>
      <div class="chat-meta-line"><button class="chat-retry secondary-button" data-query="${query}">重试</button></div>
    </div></div>`;
  }
  const thinking = message.reasoning
    ? `<details class="chat-thinking"><summary>思考过程</summary><div class="chat-md">${mdToHtml(message.reasoning)}</div></details>`
    : "";
  const trace = message.tool_trace && message.tool_trace.length
    ? `<details class="chat-trace"><summary>工具轨迹（${message.tool_trace.length} 步）</summary>
        <div class="chat-trace-list">${message.tool_trace.map(traceItemHtml).join("")}</div></details>`
    : "";
  const content = rawToolMarkup(message.content)
    ? `<div class="chat-error">模型返回了未解析的工具调用协议，本轮没有生成可用回答。请重试。</div>`
    : message.content
    ? `<div class="chat-md">${mdToHtml(message.content)}</div>`
    : `<div class="empty chat-degraded">未生成回答（未配置生成模型）。</div>`;
  const truncated = message.finish_reason === "length"
    ? `<span class="chat-truncated">回答被截断（max_tokens 不足）</span>` : "";
  const hasFulltext = (message.chunks || []).some((chunk) => chunk.chunk_id);
  const regen = message.content && hasFulltext
    ? `<button class="chat-regen secondary-button" data-mid="${message.message_id}">重新生成</button>` : "";
  const model = message.model ? ` · ${escapeHtml(message.model)}` : "";
  const topk = message.top_k != null ? ` · top_k=${message.top_k}` : "";
  return `<div class="chat-msg chat-msg-assistant"><div class="chat-bubble-assistant">
    ${thinking}${trace}${content}<button class="text-button save-research-note" data-mid="${escapeHtml(message.message_id)}">保存回答与证据</button>${ingestCardHtml(message)}${organizationCardHtml(message)}${evidenceBoxHtml(message.chunks)}
    <div class="chat-meta-line"><span class="muted">${escapeHtml(fmtDateTimeFull(message.created_at))}${model}${topk}</span>${truncated}${regen}</div>
  </div></div>`;
}

function organizationCardHtml(message) {
  const runs = [...new Set((message.tool_trace || []).map(step => step.organization_run_id).filter(Boolean))];
  return runs.map(run => `<div class="ingest-card organization-chat-card" data-run-id="${escapeHtml(run)}"><div class="ingest-card-desc">正在恢复集合草稿…</div></div>`).join("");
}

function organizationMatrixHtml(plan) {
  const kind = plan.operation === "assign" ? "指定加入" : "智能分类";
  if (plan.status !== "draft") {
    return `<strong>${kind} · ${plan.status === "applied" ? "已完成" : "已过期或失败"}</strong><p>范围：${(plan.scope || []).length} 篇；实际新增 ${plan.result?.added || 0} 条，已存在 ${plan.result?.already_present || 0} 条。</p>${(plan.result?.details || []).map(x => `<div>${escapeHtml((plan.suggestions || []).find(s => s.entity_id === x.entity_id)?.title || x.entity_id)} → ${escapeHtml((plan.rules || []).find(r => r.collection_id === x.collection_id)?.name || "集合")} · ${x.status === "added" ? "已加入" : "已存在"}</div>`).join("")}`;
  }
  const label = {include: "加入", exclude: "不建议", review: "待复核"};
  const rows = (plan.suggestions || []).map(item => `<tr><td>${escapeHtml(item.title)}</td><td>${escapeHtml(item.collection_name)}</td><td>${item.already_present ? "已存在" : label[item.decision]}</td>${plan.operation === "assign" ? "" : `<td><details><summary>规则、理由与证据</summary><p>${escapeHtml((plan.rules || []).find(r => r.collection_id === item.collection_id)?.rule || "用户指定归属")}</p><p>${escapeHtml(item.reason)}</p>${(item.evidence || []).map(e => `<blockquote>${escapeHtml(e.text_preview || e.text || "")}<br>${escapeHtml(e.chunk_id || "")}</blockquote>`).join("")}</details></td>`}<td><input class="organization-select" type="checkbox" data-entity="${escapeHtml(item.entity_id)}" data-collection="${escapeHtml(item.collection_id)}" ${item.decision === "include" || item.already_present ? "checked" : ""} ${item.decision === "exclude" || item.already_present ? "disabled" : ""}/></td></tr>`).join("");
  return `<strong>${kind}确认卡${plan.pending_collection ? " · 创建集合并加入这些论文" : ""}</strong><p>明确范围：${(plan.scope || []).length} 篇；新增候选 ${plan.summary?.suggested_add || 0} 条；已存在 ${plan.summary?.already_present || 0} 条；待复核 ${plan.summary?.review || 0} 条。</p>${(plan.skipped || []).map(x => `<p>${escapeHtml(x.entity_id)}：${escapeHtml(x.reason)}</p>`).join("")}<table><thead><tr><th>论文范围</th><th>目标集合</th><th>操作</th>${plan.operation === "assign" ? "" : "<th>依据</th>"}<th>加入</th></tr></thead><tbody>${rows}</tbody></table><p><button class="organization-apply primary-button" ${rows ? "" : "disabled"}>确认加入勾选项</button></p>`;
}

function hydrateOrganizationCards() {
  $$(".organization-chat-card").forEach((card) => {
    if (card.dataset.hydrated) return;
    card.dataset.hydrated = "1";
    api(`/api/organization-runs/${encodeURIComponent(card.dataset.runId)}`).then((plan) => {
      card.innerHTML = organizationMatrixHtml(plan);
      const button = card.querySelector(".organization-apply");
      if (button) button.addEventListener("click", async () => {
        if (button.disabled) return;
        button.disabled = true; button.textContent = "正在确认…";
        try {
          const selected = [...card.querySelectorAll(".organization-select:checked")].map((node) => ({entity_id: node.dataset.entity, collection_id: node.dataset.collection}));
          const result = await postJson("/api/organization/apply", {run_id: plan.run_id || plan.organization_run_id || card.dataset.runId, selected});
          card.innerHTML = organizationMatrixHtml(await api(`/api/organization-runs/${encodeURIComponent(card.dataset.runId)}`));
          await refreshMembershipViews();
        } catch (error) { card.innerHTML += `<p class="chat-error">${escapeHtml(error.message || String(error))}</p>`; button.disabled = false; button.textContent = "确认加入勾选项"; }
      });
    }).catch((error) => { card.innerHTML = `<div class="chat-error">分类草稿恢复失败：${escapeHtml(error.message || String(error))}</div>`; });
  });
}

function ingestCardHtml(message) {
  const trace = message.tool_trace || [];
  const ingestCall = trace.find((t) => t.tool === "ingest_papers");
  if (!ingestCall || !ingestCall.ok) return "";
  const entityIds = (ingestCall.args && ingestCall.args.entity_ids) || [];
  if (!entityIds.length) return "";
  return `<div class="ingest-card">
    <div class="ingest-card-title">检测到入库请求 · ${entityIds.length} 篇论文</div>
    <div class="ingest-card-desc">正在检查本地 PDF、解析与索引状态；确认后才会查询外部开放来源。</div>
    <div class="ingest-card-actions">
      <button class="ingest-confirm primary-button" data-ids="${escapeHtml(entityIds.join(","))}">确认处理</button>
      <span class="muted">确认后按配置获取论文并解析，可能调用外部服务</span>
    </div>
  </div>`;
}

function hydrateIngestCards() {
  $$(".ingest-card").forEach((card) => {
    const button = card.querySelector(".ingest-confirm");
    if (!button || button.dataset.resolved) return;
    button.dataset.resolved = "1";
    api(`/api/ingest-plan?entity_ids=${encodeURIComponent(button.dataset.ids || "")}`).then((plan) => {
      const items = plan.items || [];
      const current = items.filter((item) => item.plan_status === "already_indexed").length;
      const external = items.filter((item) => item.plan_status === "needs_external_resolution").length;
      const ready = items.filter((item) => item.plan_status !== "already_indexed" && item.plan_status !== "ambiguous").length;
      const ambiguous = items.filter((item) => item.plan_status === "ambiguous").length;
      card.querySelector(".ingest-card-desc").textContent =
        `本地计划：已当前入库 ${current} 篇；可处理 ${ready} 篇；确认后为 ${external} 篇查找 arXiv/OpenAlex 来源${ambiguous ? `；${ambiguous} 篇身份待确认` : ""}。`;
      button.disabled = ready === 0;
      if (ready) button.textContent = `确认处理 ${ready} 篇${external ? `，并为 ${external} 篇查找外部来源` : ""}`;
      else button.textContent = current ? "均已当前入库" : "没有可执行论文";
    }).catch((error) => {
      card.querySelector(".ingest-card-desc").textContent = `本地计划读取失败：${error.message || error}`;
      button.disabled = true;
    });
  });
}

function traceItemHtml(step) {
  if (step.tool) {
    const args = step.args ? JSON.stringify(step.args).slice(0, 80) : "";
    const badge = step.ok ? "✓" : "✗";
    const meta = `${step.summary || ""}${step.n_results != null ? ` · ${step.n_results} 条证据` : ""}${step.duration_ms != null ? ` · ${step.duration_ms}ms` : ""}`;
    return `<div class="trace-step"><span class="trace-tool">${badge} ${escapeHtml(step.tool)}</span>
      <span class="trace-args">${escapeHtml(args)}</span>
      <span class="trace-meta muted">${escapeHtml(meta)}</span></div>`;
  }
  if (step.reasoning) {
    return `<div class="trace-think">思考 · ${escapeHtml(step.reasoning.slice(0, 140))}${step.reasoning.length > 140 ? "…" : ""}</div>`;
  }
  return `<div class="trace-step"><span class="trace-tool">第 ${escapeHtml(step.round)} 轮</span>
    <span class="trace-meta muted">${escapeHtml(step.finish_reason || "")} · ${step.duration_ms}ms</span></div>`;
}

function renderChatMessages(messages) {
  const container = $("#chat-messages");
  let lastUser = "";
  const html = messages.map((message) => {
    if (message.role === "user") lastUser = message.content;
    return messageHtml(message, lastUser);
  }).join("");
  container.innerHTML = html || `<div class="empty chat-empty">新建或选择一个对话开始提问。</div>`;
  container.querySelectorAll(".chat-md").forEach(renderMathInElementIfPresent);
  hydrateIngestCards();
  hydrateOrganizationCards();
  container.scrollTop = container.scrollHeight;
}

function scrollChat() {
  const container = $("#chat-messages");
  container.scrollTop = container.scrollHeight;
}

function appendUserBubble(query) {
  $("#chat-messages .chat-empty")?.remove();
  $("#error-banner").classList.add("hidden");
  const div = document.createElement("div");
  div.className = "chat-msg chat-msg-user";
  div.innerHTML = `<div class="chat-bubble-user">${escapeHtml(query)}</div>`;
  $("#chat-messages").appendChild(div);
}

function appendPending() {
  const div = document.createElement("div");
  div.className = "chat-msg chat-msg-assistant";
  div.id = "chat-pending";
  div.innerHTML = `<div class="chat-pending"><span class="spinner"></span> 检索并生成回答中…</div>`;
  $("#chat-messages").appendChild(div);
  scrollChat();
}

function replacePending(message, lastUserContent) {
  const el = $("#chat-pending");
  if (!el) return;
  el.outerHTML = messageHtml(message, lastUserContent);
  const md = $("#chat-messages .chat-md");
  if (md) renderMathInElementIfPresent(md);
  hydrateIngestCards();
  hydrateOrganizationCards();
  scrollChat();
}

function replacePendingError(error, query) {
  const el = $("#chat-pending");
  if (!el) return;
  el.outerHTML = `<div class="chat-msg chat-msg-assistant"><div class="chat-bubble-assistant">
    <div class="chat-error">${escapeHtml(error)}</div>
    <div class="chat-meta-line"><button class="chat-retry secondary-button" data-query="${escapeHtml(query)}">重试</button></div>
  </div></div>`;
  scrollChat();
}

async function sendChat(query, options = {}) {
  query = (query || "").trim();
  if (!query) return;
  if (!chatState.current) {
    showError(new Error("请先新建或选择一个对话"));
    return;
  }
  if (chatState.pending) return;
  const cid = chatState.current.conversation_id;
  chatState.pending = true;
  chatState.pendingTaskId = null;
  $("#chat-input").disabled = true;
  $("#chat-send").disabled = true;
  $("#chat-stop").classList.remove("hidden");
  if (!options.skipAppend) appendUserBubble(query);
  appendPending();
  try {
    const { task_id } = await postJson(`/api/chats/${cid}/ask`, { query });
    chatState.pendingTaskId = task_id;
    const result = await waitTask(task_id, "/api/chats/ask/status");
    if (chatState.pendingTaskId !== task_id) return; // Already stopped, ignore the late result
    replacePending(result.message, query);
    if (chatState.current?.conversation_id === cid) {
      const saved = await api(`/api/chats/${cid}/messages`);
      if (chatState.current?.conversation_id === cid && chatState.pendingTaskId === task_id) {
        chatState.current = saved.conversation;
        $("#chat-title").textContent = saved.conversation.title;
        $("#chat-meta").textContent = `共 ${saved.messages.length} 条消息`;
        renderChatMessages(saved.messages);
      }
    }
    if (chatState.current && chatState.current.conversation_id === cid) loadChats().catch(showError);
  } catch (error) {
    if (!chatState.pending) return; // Already stopped
    replacePendingError(error.message || String(error), query);
  } finally {
    chatState.pending = false;
    chatState.pendingTaskId = null;
    $("#chat-stop").classList.add("hidden");
    $("#chat-input").disabled = false;
    $("#chat-send").disabled = false;
    $("#chat-input").focus();
  }
}

async function stopChat() {
  if (!chatState.pending || !chatState.pendingTaskId) return;
  const taskId = chatState.pendingTaskId;
  chatState.pending = false;
  chatState.pendingTaskId = null;
  $("#chat-stop").classList.add("hidden");
  $("#chat-input").disabled = false;
  $("#chat-send").disabled = false;
  const pendingEl = $("#chat-pending");
  if (pendingEl) pendingEl.remove();
  try {
    await postJson("/api/chats/ask/cancel", { task_id: taskId });
  } catch {
    // Cancellation failure is non-blocking; the task will end on its own
  }
  loadChats().catch(showError);
}

/* ===== Edit replay (Codex truncation style + snapshot archive) ===== */

function askConfirm(text) {
  return new Promise((resolve) => {
    const overlay = $("#confirm-overlay");
    $("#confirm-text").textContent = text;
    overlay.classList.remove("hidden");
    const done = (value) => {
      overlay.classList.add("hidden");
      $("#confirm-ok").removeEventListener("click", onOk);
      $("#confirm-cancel").removeEventListener("click", onCancel);
      resolve(value);
    };
    const onOk = () => done(true);
    const onCancel = () => done(false);
    $("#confirm-ok").addEventListener("click", onOk);
    $("#confirm-cancel").addEventListener("click", onCancel);
  });
}

async function editUserMessage(messageId) {
  if (chatState.pending || !chatState.current) return;
  const bubble = document.querySelector(`.chat-bubble-user[data-mid="${messageId}"]`);
  if (!bubble) return;
  const original = bubble.querySelector(".chat-msg-content").textContent;
  const textarea = document.createElement("textarea");
  textarea.className = "chat-edit-input";
  textarea.value = original;
  textarea.rows = Math.min(5, Math.max(1, Math.ceil(original.length / 36)));
  bubble.innerHTML = "";
  bubble.appendChild(textarea);
  textarea.focus();
  textarea.setSelectionRange(textarea.value.length, textarea.value.length);

  let done = false;
  const refresh = () => {
    const data = api(`/api/chats/${chatState.current.conversation_id}/messages`);
    data.then((d) => renderChatMessages(d.messages)).catch(showError);
  };
  const commit = async (save) => {
    if (done) return;
    done = true;
    const value = save ? textarea.value.trim() : "";
    if (!save || !value || value === original) {
      refresh();
      return;
    }
    const tailCount = document.querySelectorAll(`[data-mid="${messageId}"]`).length
      ? countMessagesAfter(bubble) : 0;
    if (tailCount > 0) {
      const ok = await askConfirm(`将丢弃此后的 ${tailCount} 条消息并重新生成回答（旧内容已自动存档为历史版本，可随时恢复）。继续？`);
      if (!ok) {
        done = false;
        refresh();
        return;
      }
    }
    try {
      const result = await postJson(`/api/chats/${chatState.current.conversation_id}/rewrite`, {
        message_id: messageId, content: value,
      });
      const d = await api(`/api/chats/${chatState.current.conversation_id}/messages`);
      renderChatMessages(d.messages);
      $("#chat-input").value = "";  // Thoroughly prevent residual input from triggering a second send
      await sendChat(value, { skipAppend: true }); // Message already rendered, skip the second append
    } catch (error) {
      showError(error);
      refresh();
    }
  };
  textarea.addEventListener("keydown", (event) => {
    if (event.isComposing || event.keyCode === 229) return; // Inside IME composition: Enter confirms the candidate, not the rewrite
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); commit(true); }
    else if (event.key === "Escape") { commit(false); }
  });
}

function countMessagesAfter(bubble) {
  const messageEl = bubble.closest(".chat-msg");
  let count = 0;
  let node = messageEl.nextElementSibling;
  while (node) {
    if (node.classList && node.classList.contains("chat-msg")) count += 1;
    node = node.nextElementSibling;
  }
  return count;
}

async function openSnapshots() {
  if (!chatState.current) return;
  const cid = chatState.current.conversation_id;
  const data = await api(`/api/chats/${cid}/snapshots`);
  $("#snapshots-title").textContent = "历史版本";
  $("#snapshots-list").classList.remove("hidden");
  $("#snapshots-detail").classList.add("hidden");
  $("#snapshots-list").innerHTML = data.items.length
    ? data.items.map((s) => `<div class="snapshot-item" data-sid="${s.snapshot_id}">
        <div><strong>${escapeHtml(s.label || "历史版本")}</strong><div class="muted">${escapeHtml(fmtTime(s.created_at))} · ${s.message_count} 条消息</div></div>
        <button class="snapshot-open secondary-button chat-action" data-sid="${s.snapshot_id}">查看</button>
      </div>`).join("")
    : `<div class="empty">还没有历史版本。编辑用户消息时会自动存档。</div>`;
  $$(".snapshot-open").forEach((b) => b.addEventListener("click", () => viewSnapshot(b.dataset.sid)));
  $("#snapshots-overlay").classList.remove("hidden");
}

async function viewSnapshot(snapshotId) {
  if (!chatState.current) return;
  const cid = chatState.current.conversation_id;
  const snapshot = await api(`/api/chats/${cid}/snapshots/${snapshotId}`);
  $("#snapshots-list").classList.add("hidden");
  const detail = $("#snapshots-detail");
  detail.classList.remove("hidden");
  detail.innerHTML = `<div class="snapshot-detail-head">
      <button id="snapshot-back" class="text-button">← 返回列表</button>
      <button id="snapshot-restore" class="primary-button chat-action">恢复为副本</button>
      <span class="muted">${escapeHtml(fmtTime(snapshot.created_at))}</span></div>
    <div class="snapshot-messages">${snapshot.messages.map(snapshotMessageHtml).join("")}</div>`;
  $("#snapshot-back").addEventListener("click", () => { openSnapshots().catch(showError); });
  $("#snapshot-restore").addEventListener("click", async () => {
    const copy = await postJson(`/api/chats/${cid}/snapshots/${snapshotId}/restore`, {});
    $("#snapshots-overlay").classList.add("hidden");
    await loadChats();
    await openChat(copy.conversation_id);
    showError(new Error(`已创建副本「${copy.title || "历史版本副本"}」`));
  });
}

function snapshotMessageHtml(message) {
  if (message.role === "user") {
    return `<div class="chat-msg chat-msg-user"><div class="chat-bubble-user snapshot-readonly"><span>${escapeHtml(message.content)}</span>
      <div class="chat-meta-line"><span class="muted">${escapeHtml(fmtDateTimeFull(message.created_at))}</span></div></div></div>`;
  }
  if (message.error) {
    return `<div class="chat-msg chat-msg-assistant"><div class="chat-error">${escapeHtml(message.error)}</div></div>`;
  }
  const thinking = message.reasoning
    ? `<details class="chat-thinking"><summary>思考过程</summary><div class="chat-md">${mdToHtml(message.reasoning)}</div></details>` : "";
  const content = message.content
    ? `<div class="chat-md">${mdToHtml(message.content)}</div>`
    : `<div class="empty chat-degraded">未生成回答。</div>`;
  return `<div class="chat-msg chat-msg-assistant"><div class="chat-bubble-assistant">
    ${thinking}${content}${evidenceBoxHtml(message.chunks)}
    <div class="chat-meta-line"><span class="muted">${escapeHtml(fmtDateTimeFull(message.created_at))}${message.model ? ` · ${escapeHtml(message.model)}` : ""}</span></div>
  </div></div>`;
}

async function regenerateChat(messageId) {
  if (chatState.pending || !chatState.current) return;
  const cid = chatState.current.conversation_id;
  chatState.pending = true;
  try {
    const { task_id } = await postJson(`/api/chats/${cid}/regenerate`, { message_id: messageId });
    await waitTask(task_id, "/api/chats/ask/status");
    const data = await api(`/api/chats/${cid}/messages`);
    renderChatMessages(data.messages);
  } catch (error) {
    showError(error);
  } finally {
    chatState.pending = false;
  }
}

async function newChat() {
  if (chatState.pending) return;
  const conv = await postJson("/api/chats", {});
  chatState.current = conv;
  $("#chat-header").classList.remove("hidden");
  $("#chat-title").textContent = conv.title;
  $("#chat-meta").textContent = "新对话";
  renderChatMessages([]);
  await window.Research?.showScope(conv.conversation_id);
  loadChats().catch(showError);
  $("#chat-input").focus();
}

function bindRename() {
  const button = $("#chat-rename");
  button.addEventListener("click", () => {
    if (!chatState.current) return;
    const titleEl = $("#chat-title");
    const current = chatState.current.title || "";
    const input = document.createElement("input");
    input.className = "chat-title-input";
    input.value = current;
    input.maxLength = 60;
    titleEl.replaceWith(input);
    input.focus();
    input.select();
    let done = false;
    const commit = async (save) => {
      if (done) return;
      done = true;
      const value = save ? input.value.trim() : "";
      if (save && value && value !== current) {
        try {
          chatState.current = await api(`/api/chats/${chatState.current.conversation_id}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: value }),
          });
        } catch (error) {
          showError(error);
        }
      }
      const strong = document.createElement("strong");
      strong.id = "chat-title";
      strong.textContent = chatState.current ? (chatState.current.title || "新对话") : "新对话";
      input.replaceWith(strong);
      loadChats().catch(showError);
    };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") commit(true);
      else if (event.key === "Escape") commit(false);
    });
    input.addEventListener("blur", () => commit(true));
  });
}

async function archiveToggleChat() {
  if (!chatState.current) return;
  const nextStatus = chatState.current.status === "archived" ? "active" : "archived";
  chatState.current = await api(`/api/chats/${chatState.current.conversation_id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status: nextStatus }),
  });
  if (nextStatus === "archived") {
    chatState.current = null;
    $("#chat-header").classList.add("hidden");
    renderChatMessages([]);
  } else {
    $("#chat-meta").textContent = "已恢复";
  }
  updateArchiveLabel();
  loadChats().catch(showError);
}

async function deleteChat() {
  if (!chatState.current) return;
  const conversation = chatState.current;
  await api(`/api/chats/${conversation.conversation_id}`, { method: "DELETE" });
  chatState.current = null;
  chatState.selected.delete(conversation.conversation_id);
  $("#chat-header").classList.add("hidden");
  renderChatMessages([]);
  renderChatBatchBar();
  loadChats().catch(showError);
}

async function batchArchiveChats(status) {
  const ids = [...chatState.selected];
  if (!ids.length) return;
  for (const cid of ids) {
    try {
      await api(`/api/chats/${cid}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
    } catch { /* Continue on individual failure */ }
  }
  chatState.selected.clear();
  renderChatBatchBar();
  if (chatState.current && ids.includes(chatState.current.conversation_id)) {
    chatState.current = null;
    $("#chat-header").classList.add("hidden");
    renderChatMessages([]);
  }
  loadChats().catch(showError);
}

async function batchDeleteChats() {
  const ids = [...chatState.selected];
  if (!ids.length) return;
  for (const cid of ids) {
    try { await api(`/api/chats/${cid}`, { method: "DELETE" }); } catch { /* Continue on individual failure */ }
  }
  chatState.selected.clear();
  renderChatBatchBar();
  if (chatState.current && ids.includes(chatState.current.conversation_id)) {
    chatState.current = null;
    $("#chat-header").classList.add("hidden");
    renderChatMessages([]);
  }
  loadChats().catch(showError);
}

function bindChatEvents() {
  $("#chat-new-button").addEventListener("click", () => newChat().catch(showError));
  $("#chat-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("#chat-input");
    const text = input.value;
    if (!text.trim()) return;
    if (!chatState.current) { showError(new Error("请先新建或选择一个对话")); return; }
    if (chatState.pending) return;
    input.value = "";  // Clear immediately so the user sees "sent", rather than waiting for the whole generation
    sendChat(text);
  });
  $("#chat-input").addEventListener("keydown", (event) => {
    if (event.isComposing || event.keyCode === 229) return; // Chinese IME composition: Enter confirms the candidate, does not trigger send
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("#chat-form").dispatchEvent(new Event("submit"));
    }
  });
  $("#chat-stop").addEventListener("click", () => stopChat());
  bindRename();
  $("#chat-snapshots-button").addEventListener("click", () => openSnapshots().catch(showError));
  $("#snapshots-close").addEventListener("click", () => $("#snapshots-overlay").classList.add("hidden"));
  $("#chat-archive-toggle").addEventListener("click", () => archiveToggleChat().catch(showError));
  // Delete: in-page two-stage confirmation (window.confirm is blocked and unusable inside the sandbox iframe)
  armDangerButton($("#chat-delete"), "确认删除？", () => deleteChat().catch(showError));
  armDangerButton($("#chat-batch-delete"), () => `确认删除 ${chatState.selected.size} 个对话？`, () => batchDeleteChats().catch(showError));
  $("#chat-batch-archive").addEventListener("click", () => batchArchiveChats("archived").catch(showError));
  $("#chat-batch-clear").addEventListener("click", () => { chatState.selected.clear(); renderChatBatchBar(); loadChats().catch(showError); });
  // Delegated click/check handling for the chat list (chat-item lives in #chat-list; #chat-messages is the message area)
  const bindChatSelection = (container) => {
    container.addEventListener("click", (event) => {
      const checkbox = event.target.closest(".chat-select");
      if (checkbox) return; // Hand off to the change handler to avoid also triggering openChat
      const item = event.target.closest(".chat-item");
      if (item) openChat(item.dataset.cid).catch(showError);
    });
    container.addEventListener("change", (event) => {
      const box = event.target.closest(".chat-select");
      if (!box) return;
      if (box.checked) chatState.selected.add(box.dataset.cid);
      else chatState.selected.delete(box.dataset.cid);
      renderChatBatchBar();
    });
  };
  bindChatSelection($("#chat-list"));
  bindChatSelection($("#chat-archived-list"));
  $("#chat-messages").addEventListener("click", (event) => {
    const cite = event.target.closest(".cite");
    if (cite) {
      const bubble = cite.closest(".chat-bubble-assistant");
      const details = bubble && bubble.querySelector(".chat-evidence");
      const item = bubble && bubble.querySelector(`[data-evidence="${cite.dataset.cite}"]`);
      if (details) details.open = true;
      if (item) {
        item.classList.add("chat-evidence-flash");
        item.scrollIntoView({ behavior: "smooth", block: "center" });
        setTimeout(() => item.classList.remove("chat-evidence-flash"), 1800);
      }
      return;
    }
    const retry = event.target.closest(".chat-retry");
    if (retry) { sendChat(retry.dataset.query); return; }
    const regen = event.target.closest(".chat-regen");
    if (regen) { regenerateChat(regen.dataset.mid).catch(showError); return; }
    const editBtn = event.target.closest(".chat-edit-btn");
    if (editBtn) { editUserMessage(editBtn.dataset.mid); return; }
    const ingestBtn = event.target.closest(".ingest-confirm");
    if (ingestBtn) { confirmIngest(ingestBtn.dataset.ids, ingestBtn).catch(showError); return; }
  });
}

async function confirmIngest(entityIdsCsv, button = null) {
  if (!chatState.current) return;
  if (button && button.dataset.submitting === "1") return;
  const entityIds = (entityIdsCsv || "").split(",").filter(Boolean);
  if (!entityIds.length) return;
  if (button) {
    button.dataset.submitting = "1";
    button.disabled = true;
  }
  if (!(await askConfirm(`确认在本地执行入库？将下载 ${entityIds.length} 篇论文 PDF 并解析嵌入（可能需要几分钟）。`))) {
    if (button) { button.dataset.submitting = ""; button.disabled = false; }
    return;
  }
  const cid = chatState.current.conversation_id;
  if (button) {
    button.textContent = "入库任务处理中…";
  }
  try {
    const { task_id } = await postJson(`/api/chats/${cid}/ingest`, { entity_ids: entityIds });
    await waitTask(task_id, "/api/chats/ask/status");
    // The user may keep chatting or switch conversations while ingest runs.
    // Only the originating, still-active chat may update the visible message list.
    if (chatState.current?.conversation_id === cid && !chatState.pending) {
      const d = await api(`/api/chats/${cid}/messages`);
      if (chatState.current?.conversation_id === cid && !chatState.pending) renderChatMessages(d.messages);
    }
    loadChats().catch(showError);
  } finally {
    if (button) button.dataset.submitting = "done";
  }
}

function armDangerButton(button, armedLabel, onConfirm) {
  if (!button) return;
  const label = button.textContent;
  let armed = false;
  let timer = null;
  button.addEventListener("click", () => {
    if (!armed) {
      armed = true;
      button.textContent = typeof armedLabel === "function" ? armedLabel() : armedLabel;
      button.classList.add("danger-armed");
      timer = setTimeout(() => {
        armed = false;
        button.textContent = label;
        button.classList.remove("danger-armed");
      }, 3000);
      return;
    }
    clearTimeout(timer);
    armed = false;
    button.textContent = label;
    button.classList.remove("danger-armed");
    onConfirm();
  });
}

async function waitTask(taskId, statusUrl) {
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    const state = await api(`${statusUrl}?task_id=${taskId}`);
    if (state.status === "done") return state;
    if (state.status === "error") throw new Error(state.error || "任务失败");
    if (state.status === "cancelled") throw new Error("任务已取消");
  }
}

function selectedPlanEntities() {
  const entityIds = [...state.planSelection];
  if (!state.currentCollectionId || planCollectionId !== state.currentCollectionId || entityIds.some((id) => !planEntityIds.has(id))) {
    showError(new Error("勾选范围与当前集合计划不一致，请刷新计划后重新勾选"));
    return null;
  }
  return entityIds;
}

async function runPipeline() {
  const entityIds = selectedPlanEntities();
  if (!entityIds) return;
  if (!entityIds.length) { showError(new Error("请先勾选要处理的论文")); return; }
  const steps = $$(".step-check").filter((cb) => cb.checked).map((cb) => cb.dataset.step);
  if (!steps.length) { showError(new Error("请至少勾选一个步骤")); return; }
  const defs = {
    download: ["下载", () => postJson("/api/downloads", { entity_ids: entityIds }), "/api/downloads/status"],
    parse: ["解析", () => postJson("/api/parse", { entity_ids: entityIds }), "/api/parse/status"],
    ingest: ["入库", () => postJson("/api/rag/ingest", { entity_ids: entityIds }), "/api/rag/ingest/status"],
  };
  $("#pipeline-progress").classList.remove("hidden");
  for (const step of steps) {
    const [label, start, statusUrl] = defs[step];
    $("#pipeline-progress").innerHTML = `<div class="progress-meta"><span>${label}中…</span></div>`;
    try {
      const { task_id } = await start();
      await waitTask(task_id, statusUrl);
    } catch (error) {
      $("#pipeline-progress").innerHTML = `<div class="progress-meta progress-error"><span>${escapeHtml(`${label}失败：${error.message || error}`)}</span></div>`;
      return;
    }
  }
  $("#pipeline-progress").innerHTML = `<div class="progress-meta"><span>全部完成 ✓</span></div>`;
  await loadDownloadPlan().catch(showError);
}

async function runCleanup(action) {
  const entityIds = selectedPlanEntities();
  if (!entityIds) return;
  if (!entityIds.length) { showError(new Error("请先勾选要清理的论文")); return; }
  const collectionId = state.currentCollectionId;
  await postJson("/api/cleanup", { entity_ids: entityIds, action });
  if (collectionId !== state.currentCollectionId) return;
  state.planSelection.clear();
  await loadDownloadPlan().catch(showError);
}

async function singleDelete(entityId, action) {
  const url = action === "pdf" ? `/api/downloads/${entityId}` : action === "parse" ? `/api/parse/${entityId}` : `/api/rag/${entityId}`;
  await api(url, { method: "DELETE" });
  await loadDownloadPlan();
}

const VIEW_TITLES = { rag: "科研助手", library: "论文库", insights: "数据洞察" };
const VIEW_GROUPS = { research: ["research-view"], rag: ["rag-view"], library: ["library-view"], insights: ["insights-view"] };
const INSIGHT_TABS = [["overview", "概览", "overview-view"], ["topics", "方向趋势", "topics-view"],
  ["quality", "数据质量", "quality-view"], ["entities", "论文实体", "entities-view"], ["details", "分类/venue 详情", "details-view"]];
const LIBRARY_TABS = [["catalog", "公共目录", "papers-view"], ["collections", "我的论文", "collections-view"]];

// English chrome used when I18N is set to "en" (default stays Chinese).
const VIEW_TITLES_EN = { rag: "Research Assistant", library: "Paper Library", insights: "Data Insights" };
const INSIGHT_TABS_EN = [["overview", "Overview", "overview-view"], ["topics", "Topics & trends", "topics-view"],
  ["quality", "Data quality", "quality-view"], ["entities", "Paper entities", "entities-view"], ["details", "Topic / venue detail", "details-view"]];
const LIBRARY_TABS_EN = [["catalog", "Catalog", "papers-view"], ["collections", "My papers", "collections-view"]];
const _EN_LOOKUP = { "library-view": LIBRARY_TABS_EN, "insights-view": INSIGHT_TABS_EN };
const _ZH_LOOKUP = { "library-view": LIBRARY_TABS, "insights-view": INSIGHT_TABS };

function subTabLabel(group, key) {
  const zh = _ZH_LOOKUP[group].find((t) => t[0] === key) || [];
  const en = _EN_LOOKUP[group].find((t) => t[0] === key) || [];
  const useEn = window.I18N && window.I18N.isEn();
  return useEn ? (en[1] || zh[1]) : zh[1];
}

function viewTitle(view) {
  if (view === "research") return "研究资料";
  const useEn = window.I18N && window.I18N.isEn();
  const fallback = useEn ? "Research Assistant" : "科研助手";
  return (useEn ? VIEW_TITLES_EN[view] : VIEW_TITLES[view]) || fallback;
}

// Called when the UI language changes: refresh JS-generated chrome (page title, sub-tab labels).
function applyDynamicChrome() {
  if (!window.I18N) return;
  const active = document.querySelector(".nav-item.active");
  if (active && active.dataset.view) $("#page-title").textContent = viewTitle(active.dataset.view);
  $$(".sub-tab").forEach((button) => {
    button.textContent = subTabLabel(button.dataset.group, button.dataset.tab);
  });
}

function mountGroupedViews() {
  const main = document.querySelector("main");
  const groups = [
    { id: "library-view", tabs: LIBRARY_TABS, hasCollectionBar: true },
    { id: "insights-view", tabs: INSIGHT_TABS, hasCollectionBar: false },
  ];
  groups.forEach((group) => {
    const shell = document.createElement("section");
    shell.id = group.id;
    shell.className = "view";
    const tabBar = document.createElement("div");
    tabBar.className = "sub-tabs";
    group.tabs.forEach(([key, label, sectionId]) => {
      const button = document.createElement("button");
      button.className = "sub-tab";
      button.dataset.group = group.id;
      button.dataset.tab = key;
      button.textContent = subTabLabel(group.id, key);
      tabBar.appendChild(button);
    });
    shell.appendChild(tabBar);
    if (group.hasCollectionBar) {
      const bar = document.createElement("div");
      bar.id = "library-collections-bar";
      bar.className = "library-collections-bar hidden";
      shell.appendChild(bar);
    }
    group.tabs.forEach(([key, label, sectionId]) => {
      const section = document.getElementById(sectionId);
      if (!section) return;
      section.dataset.subtab = key;
      section.classList.replace("view", "subview");
      shell.appendChild(section);
    });
    main.insertBefore(shell, document.getElementById("rag-view"));
  });
}

function activateSubTab(group, tab) {
  const shell = document.getElementById(group);
  if (!shell) return;
  // Supports both call forms: "insights-view" (container id) and "insights" (group name)
  const base = group.endsWith("-view") ? group.slice(0, -"-view".length) : group;
  shell.querySelectorAll(".sub-tab").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  shell.querySelectorAll(".subview").forEach((section) => section.classList.toggle("active", section.dataset.subtab === tab));
  updateSelectionBar();
  if (base === "library" && tab === "collections") { loadCollections().catch(showError); loadLocalLibrary().catch(showError); }
  if (base === "insights") {
    if (tab === "overview" && !$("#summary-cards").children.length) loadOverview().catch(showError);
    if (tab === "topics" && !$("#topic-cards").children.length) loadTopics().catch(showError);
    if (tab === "quality" && !$("#quality-cards").children.length) loadQuality().catch(showError);
    if (tab === "entities" && !$("#entity-cards").children.length) loadEntities().catch(showError);
    if (tab === "details") renderCurrentDetail().catch(showError);
  }
}

function bindSubTabs() {
  $$(".sub-tab").forEach((button) => button.addEventListener("click", () => activateSubTab(button.dataset.group, button.dataset.tab)));
}

function switchView(view) {
  $("#error-banner").classList.add("hidden");
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach((item) => item.classList.toggle("active", (VIEW_GROUPS[view] || []).includes(item.id)));
  $("#page-title").textContent = viewTitle(view);
  if (view === "rag" && !$("#chat-list").children.length) loadChats().catch(showError);
  if (view === "library") {
    loadPapers().catch(showError);
    loadCollections().catch(showError);
    activateSubTab("library-view", "catalog");
  }
  if (view === "research") window.Research?.load().catch(showError);
  if (view === "insights") activateSubTab("insights-view", "overview");
  updateSelectionBar();
}

function bindEvents() {
  mountGroupedViews();
  window.addEventListener("apex:langchange", applyDynamicChrome);
  applyDynamicChrome(); // reflect a persisted language choice on first paint
  bindSubTabs();
  bindDetailCrossLinks();
  activateSubTab("library-view", "catalog");
  activateSubTab("insights-view", "overview");
  switchView("rag");
  $$(".nav-item").forEach((item) => item.addEventListener("click", () => switchView(item.dataset.view)));
  $$('[data-go="papers"]').forEach((item) => item.addEventListener("click", () => { switchView("library"); activateSubTab("library-view", "catalog"); }));
  $("#filters").addEventListener("submit", (event) => { event.preventDefault(); state.page = 1; loadPapers().catch(showError); });
  $("#local-library-refresh").addEventListener("click", () => loadLocalLibrary().catch(showError));
  $("#select-all-results").addEventListener("click", () => selectAllPaperResults().catch(showError));
  $("#reset-filters").addEventListener("click", () => { $("#filters").reset(); state.page = 1; loadPapers().catch(showError); });
  $("#prev-page").addEventListener("click", () => { state.page -= 1; loadPapers().catch(showError); });
  $("#next-page").addEventListener("click", () => { state.page += 1; loadPapers().catch(showError); });
  $("#entity-filters").addEventListener("submit", (event) => { event.preventDefault(); state.entityPage = 1; loadEntities().catch(showError); });
  $("#reset-entity-filters").addEventListener("click", () => { $("#entity-filters").reset(); state.entityPage = 1; loadEntities().catch(showError); });
  $("#prev-entity-page").addEventListener("click", () => { state.entityPage -= 1; loadEntities().catch(showError); });
  $("#next-entity-page").addEventListener("click", () => { state.entityPage += 1; loadEntities().catch(showError); });
  $("#topic-filters").addEventListener("submit", (event) => { event.preventDefault(); state.topicPage = 1; loadTopicPapers().catch(showError); });
  $("#select-all-topic-results").addEventListener("click", () => selectAllTopicResults().catch(showError));
  $("#reset-topic-filters").addEventListener("click", () => { $("#topic-filters").reset(); state.topicPage = 1; loadTopicPapers().catch(showError); });
  $("#prev-topic-page").addEventListener("click", () => { state.topicPage -= 1; loadTopicPapers().catch(showError); });
  $("#next-topic-page").addEventListener("click", () => { state.topicPage += 1; loadTopicPapers().catch(showError); });
  $("#collection-create-form").addEventListener("submit", (event) => createCollectionFromForm(event).catch(showError));
  $("#selection-add").addEventListener("click", () => openPicker().catch(showError));
  $("#selection-clear").addEventListener("click", () => { state.selection.clear(); $$(".select-entity").forEach((checkbox) => { checkbox.checked = false; }); updateSelectionBar(); });
  $("#picker-cancel").addEventListener("click", closePicker);
  $("#picker-confirm").addEventListener("click", () => confirmPicker().catch(showError));
  $("#download-plan-button").addEventListener("click", () => loadDownloadPlan().catch(showError));
  $("#run-pipeline-button").addEventListener("click", () => runPipeline().catch(showError));
  $("#select-all-papers").addEventListener("change", () => {
    const checked = $("#select-all-papers").checked;
    $$(".plan-select").forEach((checkbox) => {
      checkbox.checked = checked;
      if (checked) state.planSelection.add(checkbox.dataset.entity);
      else state.planSelection.delete(checkbox.dataset.entity);
    });
  });
  $$(".cleanup-button").forEach((button) => {
    if (button.dataset.action === "all") {
      armDangerButton(button, "确认全部删除？", () => runCleanup("all").catch(showError));
    } else {
      button.addEventListener("click", () => runCleanup(button.dataset.action).catch(showError));
    }
  });
  $("#my-paper-search").oninput = renderMyPapers;
  $("#my-paper-status").onchange = renderMyPapers;
  bindChatEvents();
  api("/api/rag/status").then(config => {
    const node = document.querySelector("#chat-config-status");
    if (node) node.textContent = config.error || "";
  }).catch(() => {
    const node = document.querySelector("#chat-config-status");
    if (node) node.textContent = "配置状态读取失败";
  });
}

bindEvents();
loadOverview().catch(showError);
