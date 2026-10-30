// ==UserScript==
// @name         speakr 敏感词气泡高亮 (sensitive-lite)
// @namespace    sensitive-lite
// @version      0.1
// @description  详情页高亮命中敏感词的转写段落/气泡。数据来源：备注中的 <!-- sensitive-lite:data ... --> 块（用浏览器登录态读取，不暴露 Token）。
// @match        http://82.156.1.74:8899/*
// @run-at       document-idle
// @grant        none
// ==/UserScript==
(function () {
  "use strict";
  var COLORS = { L1: "#dc2626", L2: "#ea580c", L3: "#ca8a04" };
  var m = location.pathname.match(/\/recordings\/(\d+)/);
  if (!m) return;
  var rid = m[1];

  fetch("/api/v1/recordings/" + rid + "/notes", { headers: { Accept: "application/json" } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      var notes = (d && d.notes) || "";
      var mm = notes.match(/<!--\s*sensitive-lite:data\s+([\s\S]*?)-->/);
      if (!mm) return;
      var data;
      try { data = JSON.parse(mm[1]); } catch (e) { return; }
      if (String(data.rid) !== rid) return;
      var byIdx = {};
      (data.hits || []).forEach(function (h) { if (!(h.i in byIdx)) byIdx[h.i] = h; });
      waitForSegments(byIdx, Date.now());
    })
    .catch(function () {});

  // 转写段落由前端 Vue 渲染，轮询等待 DOM 出现
  function waitForSegments(byIdx, t0) {
    var els = document.querySelectorAll("[data-segment-index]");
    if (els.length) return apply(els, byIdx);
    if (Date.now() - t0 > 15000) return;
    setTimeout(function () { waitForSegments(byIdx, t0); }, 500);
  }

  function apply(els, byIdx) {
    var n = 0;
    els.forEach(function (el) {
      var h = byIdx[el.getAttribute("data-segment-index")];
      if (!h) return;
      n++;
      var c = COLORS[h.l] || COLORS.L3;
      el.style.outline = "2px solid " + c;
      el.style.outlineOffset = "-1px";
      el.style.borderRadius = "6px";
      el.style.backgroundColor = c + "1a"; // 10% 透明度
      el.title = "敏感词-" + h.l + (h.w ? ": " + h.w : "");
    });
    if (n) badge(n);
  }

  function badge(n) {
    var b = document.createElement("div");
    b.textContent = "⚠ 敏感词 " + n + " 处";
    b.style.cssText = "position:fixed;top:12px;right:12px;z-index:9999;background:#b91c1c;color:#fff;"
      + "padding:6px 10px;border-radius:8px;font-size:13px;box-shadow:0 2px 8px rgba(0,0,0,.25);cursor:pointer";
    b.onclick = function () { b.remove(); };
    document.body.appendChild(b);
  }
})();
