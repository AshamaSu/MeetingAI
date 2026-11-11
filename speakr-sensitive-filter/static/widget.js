/* speakr 敏感词气泡高亮 — sensitive-filter Part B（方案 B7）
 * 数据源双通道：① 检测服务 /hits?recording_id=N（秒级，需浏览器可达 8898）
 *              ② speakr notes 中的机器可读块（浏览器登录态，回退通道）
 * 注入：speakr templates/includes/loading_overlay.html 引用本文件。
 * 展示：语句级 = 气泡左侧粗竖条 + 📌 角标；关键词级 = <mark> 下划线着色。
 */
(function () {
  "use strict";
  var script = document.currentScript;
  var API = window.SENSITIVE_FILTER_URL ||
    (script && script.src ? script.src.replace(/\/widget\.js.*$/, "") : "") || "";
  var m = location.pathname.match(/\/recordings\/(\d+)/);
  if (!m) return;
  var rid = m[1];

  var RANK = { L1: 1, L2: 2, L3: 3 };
  var HITS = [];

  // ---------- 样式 ----------
  var css = [
    ".sw-hit{border-left:4px solid #d1d5db !important;}",
    ".sw-hit.sw-L1{border-left-color:#dc2626 !important;}",
    ".sw-hit.sw-L2{border-left-color:#ea580c !important;}",
    ".sw-hit.sw-L3{border-left-color:#ca8a04 !important;}",
    ".sw-hit .speaker-bubble-content::before{content:'\uD83D\uDCCC';margin-right:4px;font-size:.85em;}",
    "mark.sw-term{background:transparent;color:inherit;font-weight:600;padding:0 1px;border-radius:2px;}",
    "mark.sw-term.sw-L1{border-bottom:2px solid #dc2626;}",
    "mark.sw-term.sw-L2{border-bottom:2px solid #ea580c;}",
    "mark.sw-term.sw-L3{border-bottom:2px solid #ca8a04;}"
  ].join("\n");
  var style = document.createElement("style");
  style.textContent = css;
  (document.head || document.documentElement).appendChild(style);

  // ---------- 数据 ----------
  function fetchJSON(url) {
    return fetch(url, { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error(String(r.status))); });
  }

  function fromFilter() {
    if (!API) return Promise.reject(new Error("no filter api"));
    return fetchJSON(API + "/hits?recording_id=" + rid).then(function (d) {
      if (String(d.rid) !== rid) throw new Error("rid mismatch");
      return d.hits || [];
    });
  }

  function fromNotes() {
    return fetchJSON("/api/v1/recordings/" + rid + "/notes").then(function (d) {
      var notes = (d && d.notes) || "";
      var mm = notes.match(/<!--\s*sensitive-lite:data\s+([\s\S]*?)-->/);
      if (!mm) return [];
      var data = JSON.parse(mm[1]);
      return String(data.rid) === rid ? (data.hits || []) : [];
    });
  }

  function load() {
    fromFilter().catch(function () { return fromNotes(); }).then(function (hits) {
      HITS = hits || [];
      applyAll();
    }).catch(function () { /* 两通道都不可用则静默 */ });
  }

  // ---------- 渲染 ----------
  function collect() {
    var byIdx = {};
    HITS.forEach(function (h) {
      var o = byIdx[h.i] || (byIdx[h.i] = { rank: 99, level: null, forms: [] });
      var r = RANK[h.l] || 99;
      if (r < o.rank) { o.rank = r; o.level = h.l; }
      [h.w].concat(h.v || []).forEach(function (f) {
        if (f && o.forms.indexOf(String(f)) < 0) o.forms.push(String(f));
      });
    });
    return byIdx;
  }

  function bubbleOf(el) {
    if (el.classList.contains("speaker-bubble")) return el;
    var q = el.querySelector(".speaker-bubble");
    if (q) return q;
    var up = el.closest ? el.closest(".speaker-bubble") : null;
    return up || el;
  }

  function wrapTerms(content, forms, level) {
    if (!content || content.getAttribute("data-sw-done") === level) return;
    var walker = document.createTreeWalker(content, NodeFilter.SHOW_TEXT, null);
    var nodes = [], n;
    while ((n = walker.nextNode())) {
      if (!n.nodeValue || !n.nodeValue.trim()) continue;
      var p = n.parentElement;
      if (p && (p.tagName === "MARK" || p.closest("mark.sw-term"))) continue;
      nodes.push(n);
    }
    var hitAny = false;
    nodes.forEach(function (node) {
      var text = node.nodeValue;
      var lower = text.toLowerCase();
      var best = null;
      for (var k = 0; k < forms.length; k++) {
        var pos = lower.indexOf(forms[k].toLowerCase());
        if (pos >= 0 && (best === null || pos < best.pos)) best = { pos: pos, form: forms[k] };
      }
      if (best === null) return;
      hitAny = true;
      var mark = document.createElement("mark");
      mark.className = "sw-term sw-" + level;
      mark.textContent = text.substr(best.pos, best.form.length);
      var after = text.substr(best.pos + best.form.length);
      node.nodeValue = text.slice(0, best.pos);
      node.parentNode.insertBefore(mark, node.nextSibling);
      if (after) node.parentNode.insertBefore(document.createTextNode(after), mark.nextSibling);
    });
    if (hitAny || content.querySelector("mark.sw-term")) content.setAttribute("data-sw-done", level);
  }

  function applyAll() {
    var byIdx = collect();
    var nodes = document.querySelectorAll("[data-segment-index]");
    for (var k = 0; k < nodes.length; k++) {
      var el = nodes[k];
      var o = byIdx[el.getAttribute("data-segment-index")];
      if (!o || !o.level) continue;
      var target = bubbleOf(el);
      if (!target.classList.contains("sw-hit")) target.classList.add("sw-hit", "sw-" + o.level);
      wrapTerms(target.querySelector(".speaker-bubble-content") || target, o.forms, o.level);
    }
  }

  // ---------- 时序：SPA 重渲染兜底 ----------
  var mo = null, timer = null;
  function schedule() {
    if (timer) return;
    timer = setTimeout(function () { timer = null; applyAll(); }, 300);
  }
  function observe() {
    if (mo || !document.body) return;
    mo = new MutationObserver(schedule);
    mo.observe(document.body, { childList: true, subtree: true });
  }
  function waitAndApply(tries) {
    if (document.querySelector("[data-segment-index]")) { applyAll(); observe(); return; }
    if (tries <= 0) { observe(); return; }
    setTimeout(function () { waitAndApply(tries - 1); }, 500);
  }

  waitAndApply(24);
  load();
})();
