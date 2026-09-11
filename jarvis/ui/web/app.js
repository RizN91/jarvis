/* ==========================================================================
   Jarvis — Settings & Setup UI
   Vanilla JS. No frameworks, no network, no inline handlers.
   Talks to Python through window.pywebview.api only.
   ========================================================================== */
(function () {
  "use strict";

  /* ── 1. Small helpers ──────────────────────────────────────────────── */
  var ESC_MAP = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(v) {
    return String(v === null || v === undefined ? "" : v).replace(/[&<>"']/g, function (c) { return ESC_MAP[c]; });
  }
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function clamp(n, a, b) { return Math.min(b, Math.max(a, n)); }
  function isNum(v) { return typeof v === "number" && isFinite(v); }
  function num(v, d) { var n = Number(v); return isFinite(n) ? n : d; }

  function usd(v) {
    var n = num(v, 0);
    if (n > 0 && n < 0.01) return "$" + n.toFixed(4);
    return "$" + n.toFixed(2);
  }
  function fmtDur(sec) {
    var s = Math.max(0, Math.round(num(sec, 0)));
    if (s < 60) return s + "s";
    var m = Math.floor(s / 60), r = s % 60;
    if (m < 60) return m + "m " + r + "s";
    var h = Math.floor(m / 60);
    return h + "h " + (m % 60) + "m";
  }
  function fmtUsdSigned(v) { return usd(v); }
  function pct(v, cap) { return cap > 0 ? clamp((v / cap) * 100, 0, 100) : 0; }

  /* ── 1b. Internationalisation ───────────────────────────────────────────
     Translations live in locales.js (loaded before this file as a plain
     <script>, so it works from the filesystem with no server and no fetch).
     Lookup is by ENGLISH SOURCE STRING, gettext-style: the key IS the English
     text, so a missing translation can never surface a raw dotted key — it
     falls straight back to the English words. `{name}` placeholders are
     substituted from the second argument.                                     */
  var LOCALES = (window.JARVIS_LOCALES && typeof window.JARVIS_LOCALES === "object")
    ? window.JARVIS_LOCALES : {};
  var LANG_META = (window.JARVIS_LANGS && window.JARVIS_LANGS.length)
    ? window.JARVIS_LANGS
    : [{ code: "en", name: "English", dir: "ltr" }];
  var RTL = {};
  LANG_META.forEach(function (m) { if (m.dir === "rtl") RTL[m.code] = true; });

  function normalizeLang(code) {
    if (!code) return "";
    var c = String(code);
    var i;
    for (i = 0; i < LANG_META.length; i++) if (LANG_META[i].code === c) return c;
    var low = c.toLowerCase();
    for (i = 0; i < LANG_META.length; i++) if (LANG_META[i].code.toLowerCase() === low) return LANG_META[i].code;
    var base = low.split("-")[0];
    for (i = 0; i < LANG_META.length; i++) {
      if (LANG_META[i].code.toLowerCase().split("-")[0] === base) return LANG_META[i].code;
    }
    return "";
  }

  function detectLang() {
    var saved = "";
    try { saved = window.localStorage.getItem("jarvis_ui_language") || ""; } catch (e) { saved = ""; }
    var n = normalizeLang(saved);
    if (n) return n;
    var nav = (navigator.languages && navigator.languages[0]) || navigator.language ||
      navigator.userLanguage || "en";
    return normalizeLang(nav) || "en";
  }

  var currentLang = detectLang();

  // t("English source", { placeholders }). Never renders a raw key: the key is
  // the English sentence itself, so the fallback is always readable English.
  function t(key, vars) {
    if (key === null || key === undefined) return "";
    var table = LOCALES[currentLang] || {};
    var s = table[key];
    if (s === undefined) {
      var en = LOCALES["en"] || {};
      s = en[key] !== undefined ? en[key] : key;
    }
    if (vars) {
      s = String(s).replace(/\{(\w+)\}/g, function (m, k) {
        return (vars[k] === undefined || vars[k] === null) ? m : String(vars[k]);
      });
    }
    return s;
  }

  // Translate the static markup in index.html (data-i18n / data-i18n-aria).
  function applyStaticText() {
    $$("[data-i18n]").forEach(function (el) { el.textContent = t(el.getAttribute("data-i18n")); });
    $$("[data-i18n-aria]").forEach(function (el) {
      var v = t(el.getAttribute("data-i18n-aria"));
      el.setAttribute("aria-label", v);
      if (el.hasAttribute("title")) el.setAttribute("title", v);
    });
  }

  function applyDir() {
    var el = document.documentElement;
    el.setAttribute("lang", currentLang);
    el.setAttribute("dir", RTL[currentLang] ? "rtl" : "ltr");
  }

  function langName(code) {
    for (var i = 0; i < LANG_META.length; i++) if (LANG_META[i].code === code) return LANG_META[i].name;
    return code;
  }

  // Re-render everything in the chosen language and persist it. Persistence
  // goes through the same config bridge every other setting uses
  // (`ui_language`), with localStorage as an instant-boot mirror.
  function setLang(code) {
    var n = normalizeLang(code);
    if (!n || n === currentLang) return;
    currentLang = n;
    applyDir();
    try { window.localStorage.setItem("jarvis_ui_language", n); } catch (e) {}
    // Persist through the same config bridge every other setting uses, then
    // reload. A reload is used deliberately: strings that are built once at
    // load time (the nav labels, the status maps) must come back in the new
    // language too. The switcher lives on Settings > General, and the wizard
    // runs before a language can be chosen, so nothing is lost by reloading.
    var go = function () { window.location.reload(); };
    saveConfig(prefixPatch("ui_language", n), true).then(go, go);
  }

  // Small helper: a language <select> showing each language in its own script.
  function langSelect() {
    return '<select class="select" id="s_ui_language" data-act="select" data-key="ui_language" ' +
      'aria-label="' + esc(t("Language")) + '">' +
      LANG_META.map(function (m) {
        return '<option value="' + esc(m.code) + '"' +
          (m.code === currentLang ? " selected" : "") + ">" + esc(m.name) + "</option>";
      }).join("") + "</select>";
  }

  function wave(n, cls) {
    var out = '<span class="wave ' + (cls || "") + '" aria-hidden="true">';
    for (var i = 0; i < n; i++) {
      var x = i / Math.max(1, n - 1);
      var peak = 6 + 87 * Math.exp(-Math.pow((x - .78) / .19, 2)) *
        (.30 + .70 * Math.pow(Math.sin(i * 1.73), 2)) +
        15 * Math.exp(-Math.pow((x - .38) / .13, 2));
      out += '<i class="bar" style="--i:' + i + ';--peak:' + peak.toFixed(1) + '%"></i>';
    }
    return out + "</span>";
  }

  var ICON = {
    sliders: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/></svg>',
    mic: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg>',
    spark: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/></svg>',
    key: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="6.5" width="19" height="11" rx="2.5"/><path d="M7 10h.01M11 10h.01M15 10h.01M8 14h8"/></svg>',
    wave: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/></svg>',
    tag: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M11.4 3.6H20v8.6l-8.9 8.9a1.6 1.6 0 0 1-2.3 0l-6.3-6.3a1.6 1.6 0 0 1 0-2.3z"/><circle cx="16.4" cy="7.6" r="1.3"/></svg>',
    brain: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5.5a3 3 0 0 0-6 0 3 3 0 0 0-1.6 5.4A3 3 0 0 0 6 16.6a3 3 0 0 0 6 .4z"/><path d="M12 5.5a3 3 0 0 1 6 0 3 3 0 0 1 1.6 5.4A3 3 0 0 1 18 16.6a3 3 0 0 1-6 .4z"/></svg>',
    gauge: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 18l4.2-5.2"/></svg>',
    plug: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M9 3v5M15 3v5M6.5 8h11v3a5.5 5.5 0 0 1-11 0z"/><path d="M12 16.5V21"/></svg>',
    shield: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5.5c0 4.4-2.9 8.1-7 9.5-4.1-1.4-7-5.1-7-9.5V6z"/><path d="M9.5 12.2l1.8 1.8 3.4-3.6"/></svg>',
    info: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.6"/><path d="M12 11v5.2M12 8h.01"/></svg>',
    folder: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 6.8A1.8 1.8 0 0 1 5.3 5h3.6l1.7 2.2h7.9A1.8 1.8 0 0 1 20.5 9v8.2A1.8 1.8 0 0 1 18.7 19H5.3a1.8 1.8 0 0 1-1.8-1.8z"/></svg>',
    app: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.6"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.6"/><rect x="13.5" y="13.5" width="7" height="7" rx="1.6"/></svg>',
    plus: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.5-5.8"/><path d="M20 4v4.4h-4.4"/></svg>',
    play: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4.8l11 7.2-11 7.2z"/></svg>',
    stop: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="6.5" y="6.5" width="11" height="11" rx="2"/></svg>',
    trash: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 7h15M9.5 7V5.4A1.4 1.4 0 0 1 10.9 4h2.2a1.4 1.4 0 0 1 1.4 1.4V7M6.5 7l.9 11.2A1.8 1.8 0 0 0 9.2 20h5.6a1.8 1.8 0 0 0 1.8-1.8L17.5 7"/></svg>',
    keytool: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="12" r="3.4"/><path d="M11.4 12H21M18 12v3M15 12v2.4"/></svg>',
    check: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.6l4.4 4.4L19 7.4"/></svg>',
    eye: '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M2.6 12S6.2 5.8 12 5.8 21.4 12 21.4 12 17.8 18.2 12 18.2 2.6 12 2.6 12z"/><circle cx="12" cy="12" r="2.8"/></svg>',
    eyeoff: '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4l16 16"/><path d="M9.6 6.3A9.6 9.6 0 0 1 12 6c5.8 0 9.4 6 9.4 6a15 15 0 0 1-3 3.6M6.5 8.2A15.4 15.4 0 0 0 2.6 12s3.6 6 9.4 6c1 0 1.9-.2 2.7-.5"/></svg>'
  };
  function icon(name) { return ICON[name] || ""; }

  // Glyphs the Jarvis shell needs, on the same 24x24 stroke grid as the rest.
  ICON.arrow = ICON.arrow || '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h13M13 6l6 6-6 6"/></svg>';
  ICON.bolt = ICON.bolt || '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M13 3 5 13.5h5.5L11 21l8-10.5h-5.5z"/></svg>';
  ICON.layers = ICON.layers || '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M12 3.5 3.5 8 12 12.5 20.5 8z"/><path d="M3.5 12.5 12 17l8.5-4.5"/></svg>';
  ICON.lock = ICON.lock || '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><rect x="4.6" y="10" width="14.8" height="10" rx="2.4"/><path d="M8.2 10V7.4a3.8 3.8 0 0 1 7.6 0V10"/></svg>';

  // Two glyphs the History tab needs. Same 24x24 stroke grid as the rest.
  ICON.clock = ICON.clock || '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" ' +
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true">' +
    '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>';
  ICON.copy = ICON.copy || '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" ' +
    'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<rect x="9" y="9" width="11" height="11" rx="2"/>' +
    '<path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>';

  /* ── 2. Bridge to Python ───────────────────────────────────────────── */
  var api = function (m) {
    var args = Array.prototype.slice.call(arguments, 1);
    return window.pywebview.api[m].apply(window.pywebview.api, args);
  };
  var apiReady = false;
  var apiWaiters = [];

  function apiPresent() { return !!(window.pywebview && window.pywebview.api); }

  function waitForApi(timeoutMs) {
    return new Promise(function (resolve) {
      if (apiPresent()) { apiReady = true; return resolve(true); }
      var t0 = Date.now();
      var iv = setInterval(function () {
        if (apiPresent()) {
          clearInterval(iv); apiReady = true;
          apiWaiters.forEach(function (fn) { try { fn(); } catch (e) {} });
          apiWaiters = [];
          resolve(true);
        } else if (Date.now() - t0 > (timeoutMs || 15000)) {
          clearInterval(iv); resolve(false);
        }
      }, 60);
    });
  }

  function call(method) {
    var args = Array.prototype.slice.call(arguments, 1);
    return waitForApi(15000).then(function (ok) {
      if (!ok) return null;
      return api.apply(null, [method].concat(args)).catch(function (err) {
        toast("error", method + "() failed — " + (err && err.message ? err.message : String(err)));
        return null;
      });
    });
  }

  /* ── 3. Toasts / saved affordance / live region ────────────────────── */
  var toastHost = $("#toasts");
  function toast(level, message) {
    if (!toastHost) return;
    var el = document.createElement("div");
    el.className = "toast " + (level === "warn" ? "warn" : level === "error" ? "error" : "info");
    el.setAttribute("role", level === "error" ? "alert" : "status");
    var body = document.createElement("div");
    body.className = "t-body";
    body.textContent = message;
    var x = document.createElement("button");
    x.type = "button"; x.className = "t-x"; x.setAttribute("aria-label", t("Dismiss notification"));
    x.textContent = "\u00d7";
    x.addEventListener("click", function () { kill(); });
    el.appendChild(body); el.appendChild(x);
    toastHost.appendChild(el);
    var timer = setTimeout(kill, level === "error" ? 9000 : 6000);
    var done = false;
    function kill() {
      if (done) return; done = true;
      clearTimeout(timer);
      el.classList.add("out");
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 220);
    }
  }

  var saveFlag = $("#saveflag");
  var saveTimer = null, flagTimer = null;
  function flashSaved(saving) {
    if (!saveFlag) return;
    clearTimeout(flagTimer);
    saveFlag.classList.toggle("saving", !!saving);
    saveFlag.textContent = saving ? t("Saving\u2026") : t("Saved");
    saveFlag.classList.add("show");
    if (!saving) flagTimer = setTimeout(function () { saveFlag.classList.remove("show"); }, 1800);
  }
  function announce(msg) { var l = $("#live"); if (l) l.textContent = msg; }

  /* ── 4. State ──────────────────────────────────────────────────────── */
  var S = {
    mode: "wizard",           // 'wizard' | 'settings'
    step: 0,
    section: "general",
    state: null,              // get_state() payload
    devices: null,
    usage: null,
    memory: null,
    agents: null,
    history: null,
    historyQuery: "",
    voices: null,
    commandHelp: null,
    loadedUsage: false,
    loadedMemory: false,
    loadedAgents: false,
    loadedHistory: false,
    loadedVoices: false,
    micTesting: false,
    micLevel: 0,
    status: "sleeping",
    statusDetail: "",
    calib: null,
    recorderOpen: false,
    connResult: null,
    connBusy: null,
    keyDraft: "",
    keyErr: "",
    lastTest: {}
  };

  function cfg() { return (S.state && S.state.config) || {}; }
  function val(key, fallback) {
    var v = cfg()[key];
    if (v === undefined || v === null) {
      if (S.state && S.state[key] !== undefined && S.state[key] !== null) return S.state[key];
      return fallback;
    }
    return v;
  }
  function store(key, v) { if (S.state && S.state.config) S.state.config[key] = v; }

  var debounceTimers = {};
  function debounce(key, fn, ms) {
    clearTimeout(debounceTimers[key]);
    debounceTimers[key] = setTimeout(fn, ms || 400);
  }

  function saveConfig(patch, quiet) {
    if (!quiet) flashSaved(true);
    return call("set_config", patch).then(function (res) {
      flashSaved(false);
      if (res && res.ok && res.config) {
        S.state.config = res.config;
        applyTheme();
      } else if (res && res.ok === false) {
        toast("warn", t("The app rejected that setting change."));
      }
      return res;
    });
  }

  function applyTheme() {
    var thm = val("theme", "dark") === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", thm);
    var rm = !!val("reduced_motion", false);
    document.documentElement.classList.toggle("reduced-motion", rm);
    var btn = $('[data-act="theme"]');
    if (btn) btn.setAttribute("aria-label", thm === "light" ? t("Switch to dark theme") : t("Switch to light theme"));
    var m = $('meta[name="color-scheme"]');
    if (m) m.setAttribute("content", thm === "light" ? "light dark" : "dark light");
  }

  /* ── 5. Component builders ─────────────────────────────────────────── */
  function feat(ic, title, sub) {
    return '<li><span class="feat-ic">' + icon(ic) + "</span>" +
      '<span class="feat-txt"><b>' + esc(title) + "</b><i>" + esc(sub) + "</i></span></li>";
  }

  function card(o) {
    var h = '<section class="card' + (o.cls ? " " + o.cls : "") + '">';
    if (o.eyebrow) h += '<div class="eyebrow">' + esc(o.eyebrow) + "</div>";
    if (o.title) h += '<h2 class="card-title">' + esc(o.title) +
      (o.badge ? '<span class="card-badge">' + o.badge + "</span>" : "") + "</h2>";
    if (o.desc) h += '<p class="card-desc">' + esc(o.desc) + "</p>";
    if (o.body) h += '<div class="card-body">' + o.body + "</div>";
    if (o.foot) h += '<div class="card-foot">' + o.foot + "</div>";
    return h + "</section>";
  }

  function row(label, control, hint, id) {
    return '<div class="row"><div class="row-label">' +
      (id ? '<label for="' + esc(id) + '">' + esc(label) + "</label>" : "<label>" + esc(label) + "</label>") +
      (hint ? '<p class="hint">' + esc(hint) + "</p>" : "") +
      '</div><div class="row-ctl">' + control + "</div></div>";
  }

  function toggle(key, on, label, hint) {
    var id = "tg_" + key;
    return '<div class="row"><div class="row-label"><label id="' + esc(id) + '_l">' + esc(label) + "</label>" +
      (hint ? '<p class="hint">' + esc(hint) + "</p>" : "") + "</div>" +
      '<div class="row-ctl"><button type="button" role="switch" id="' + esc(id) + '" class="switch" aria-checked="' + (on ? "true" : "false") +
      '" aria-labelledby="' + esc(id) + '_l" data-act="toggle" data-key="' + esc(key) + '"><span class="knob"></span></button></div></div>';
  }

  function seg(key, value, opts) {
    return '<div class="seg" role="radiogroup" aria-label="' + esc(key.replace(/_/g, " ")) + '">' +
      opts.map(function (o) {
        var on = String(o.v) === String(value);
        return '<button type="button" role="radio" aria-checked="' + (on ? "true" : "false") + '" class="seg-btn' + (on ? " on" : "") +
          '" data-act="seg" data-key="' + esc(key) + '" data-val="' + esc(o.v) + '">' +
          '<span class="seg-t">' + esc(o.label) + "</span>" +
          (o.hint ? '<span class="seg-h">' + esc(o.hint) + "</span>" : "") + "</button>";
      }).join("") + "</div>";
  }

  function inpHtml(key, value, o) {
    o = o || {};
    return '<input class="inp" id="in_' + esc(key) + '" type="' + esc(o.type || "text") + '" value="' + esc(value) +
      '" placeholder="' + esc(o.placeholder || "") + '" data-act="text" data-key="' + esc(key) +
      '" aria-label="' + esc(o.label || key) + '"' + (o.autocomplete ? ' autocomplete="' + esc(o.autocomplete) + '"' : "") + ">";
  }

  function numHtml(key, value, o) {
    o = o || {};
    return '<div class="numwrap">' + (o.pre ? '<span class="affix pre">' + esc(o.pre) + "</span>" : "") +
      '<input class="inp num" id="n_' + esc(key) + '" type="number" inputmode="decimal" value="' + esc(value) +
      '" min="' + esc(o.min) + '" max="' + esc(o.max) + '" step="' + esc(o.step || 1) + '" data-act="number" data-key="' + esc(key) +
      '" aria-label="' + esc(o.label || key) + '">' +
      (o.suf ? '<span class="affix suf">' + esc(o.suf) + "</span>" : "") + "</div>";
  }

  function selectHtml(key, value, options) {
    return '<select class="select" id="s_' + esc(key) + '" data-act="select" data-key="' + esc(key) +
      '" aria-label="' + esc(key.replace(/_/g, " ")) + '">' +
      options.map(function (o) {
        return '<option value="' + esc(o.v) + '"' + (String(o.v) === String(value) ? " selected" : "") + ">" + esc(o.label) + "</option>";
      }).join("") + "</select>";
  }

  function chipsHtml(key, items, o) {
    o = o || {};
    items = items || [];
    var h = '<div class="chips" id="chips_' + esc(key) + '">';
    if (!items.length) {
      h += '<span class="empty">' + esc(o.empty || t("Nothing added yet.")) + "</span>";
    } else {
      h += items.map(function (it, i) {
        var text = typeof it === "string" ? it : (it && (it.text || it.name)) || String(it);
        return '<span class="chip' + (o.teal ? " teal" : "") + '"><span class="chip-t" title="' + esc(text) + '">' + esc(text) + "</span>" +
          '<button type="button" class="chip-x" data-act="chip-del" data-key="' + esc(key) + '" data-i="' + i +
          '" aria-label="' + esc(t("Remove {name}", { name: text })) + '">\u00d7</button></span>';
      }).join("");
    }
    h += "</div>";
    h += '<form class="addrow" data-form="chip-add" data-key="' + esc(key) + '">' +
      '<input class="inp" type="text" data-input="chip-add" data-key="' + esc(key) + '" placeholder="' +
      esc(o.placeholder || t("Add an entry")) + '" aria-label="' + esc(o.aria || t("Add entry")) + '" autocomplete="off">' +
      '<button type="submit" class="btn sm">' + esc(o.addLabel || t("Add")) + "</button></form>";
    return h;
  }

  function listHtml(key, items, o) {
    o = o || {};
    items = items || [];
    var h = '<div class="list" id="list_' + esc(key) + '">';
    if (!items.length) {
      h += '<span class="empty">' + esc(o.empty || t("Nothing here yet.")) + "</span>";
    } else {
      h += items.map(function (it, i) {
        var isObj = it && typeof it === "object";
        var name = isObj ? (it.name || it.path || t("Entry")) : String(it);
        var path = isObj ? (it.path || "") : "";
        return '<div class="list-row">' + (o.badge ? '<span class="l-badge">' + esc(o.badge) + "</span>" : "") +
          '<span class="l-txt"><span class="l-name" title="' + esc(name) + '">' + esc(name) + "</span>" +
          (path && path !== name ? '<span class="l-path">' + esc(path) + "</span>" : "") + "</span>" +
          '<button type="button" class="btn sm ghost" data-act="list-del" data-key="' + esc(key) + '" data-i="' + i +
          '" aria-label="' + esc(t("Remove {name}", { name: name })) + '">' + icon("trash") + "</button></div>";
      }).join("");
    }
    h += "</div>";
    if (o.actions) h += '<div class="inline">' + o.actions + "</div>";
    return h;
  }

  function callout(kind, html) {
    return '<div class="callout ' + esc(kind) + '"><div class="co-body">' + html + "</div></div>";
  }

  function slider(key, value, o) {
    o = o || {};
    var v = num(value, o.min || 0);
    var p = ((v - (o.min || 0)) / ((o.max || 1) - (o.min || 0))) * 100;
    return '<div class="slider-wrap">' +
      '<div class="slider-head"><span>' + esc(o.leftLabel || "") + '</span><span class="slider-val" id="sv_' + esc(key) + '">' +
      esc(o.display ? o.display(v) : v) + "</span></div>" +
      '<input type="range" id="r_' + esc(key) + '" min="' + esc(o.min) + '" max="' + esc(o.max) + '" step="' + esc(o.step || 0.01) +
      '" value="' + esc(v) + '" style="--pct:' + p.toFixed(1) + '%" data-act="range" data-key="' + esc(key) +
      '" aria-label="' + esc(o.label || key) + '">' +
      '<div class="slider-head"><span class="muted">' + esc(o.lowLabel || "") + '</span><span class="muted">' + esc(o.highLabel || "") + "</span></div>" +
      "</div>";
  }

  function pageHead(title, para, badge, eyebrow) {
    return '<div class="page-head' + (badge ? " with-badge" : "") + '">' +
      (badge ? '<div class="step-badge" aria-hidden="true">' + esc(badge) + "</div>" : "") +
      (eyebrow ? '<div class="eyebrow">' + esc(eyebrow) + "</div>" : "") +
      "<h2>" + esc(title) + "</h2>" +
      (para ? "<p>" + esc(para) + "</p>" : "") + "</div>";
  }

  /* ── 6. Shared feature blocks ──────────────────────────────────────── */

  // Fingerprint readout
  function fingerprintBlock() {
    var fp = (S.state && S.state.key_fingerprint) || null;
    if (fp) {
      return '<div class="inline"><span class="fp"><span class="eyebrow">' + esc(t("Stored key")) + '</span><code>' + esc(fp) + "</code></span>" +
        '<button type="button" class="btn sm ghost" data-act="clear-key">' + esc(t("Remove key")) + '</button></div>';
    }
    return '<div class="callout warn"><div class="co-body"><p>' + esc(t("No API key is stored yet. Dictation and the assistant will not work until you add one.")) + '</p></div></div>';
  }

  // API key entry form (never echoes the key back)
  function keyForm() {
    return '<div class="stack">' +
      '<label for="apikey" class="eyebrow">' + esc(t("API key")) + '</label>' +
      '<div class="addrow">' +
      '<input class="inp" id="apikey" type="password" autocomplete="off" spellcheck="false" placeholder="sk-..." ' +
      'data-input="apikey" aria-describedby="apikey-hint" value="">' +
      '<button type="button" class="icon-btn" data-act="key-reveal" aria-label="' + esc(t("Show or hide the API key")) + '" aria-pressed="false" ' +
      'title="' + esc(t("Show or hide key")) + '">' + icon("eye") + "</button>" +
      '<button type="button" class="btn primary" data-act="key-save">' + esc(t("Save key")) + '</button>' +
      "</div>" +
      '<p class="hint small muted" id="apikey-hint">' +
      esc(t("The key is sent straight to the app and stored in Windows Credential Manager. It is never displayed again \u2014 only a short fingerprint is kept for confirmation.")) + "</p>" +
      (S.keyErr ? '<div class="callout error"><div class="co-body"><p>' + esc(S.keyErr) + "</p></div></div>" : "") +
      "</div>";
  }

  // Billable connection test
  var EST = { economy: "~$0.002", live: "~$0.02" };
  function connTestBlock(scope) {
    var res = S.connResult;
    var h = '<div class="stack">';
    h += callout("warn",
      "<p>" + t("<b>These tests are billable.</b> Jarvis sends a short sample to the speech service and you are charged for it.") + "</p>" +
      "<p>" + t("Estimated cost: <b>economy {economy}</b> per run, <b>live {live}</b> per run. Estimates only \u2014 the exact billed amount comes back from the service after each run.",
        { economy: EST.economy, live: EST.live }) + "</p>");
    h += '<div class="inline">' +
      '<button type="button" class="btn" data-act="conn-test" data-mode="economy"' + (S.connBusy ? " disabled" : "") + ">" +
      icon("play") + " " + esc(t("Run economy test (est. {est})", { est: EST.economy })) + "</button>" +
      '<button type="button" class="btn" data-act="conn-test" data-mode="live"' + (S.connBusy ? " disabled" : "") + ">" +
      icon("play") + " " + esc(t("Run live test (est. {est})", { est: EST.live })) + "</button>" +
      "</div>";
    if (S.connBusy) h += '<p class="small muted">' +
      esc(t("Running the {mode} test\u2026 this can take a few seconds.", { mode: S.connBusy === "live" ? t("live") : t("economy") })) + "</p>";
    if (res) {
      var kind = res.ok ? "ok" : "error";
      var lines = "<p><b>" + esc(res.ok ? t("Test passed") : t("Test failed")) + "</b> \u00b7 " + esc(res.ms) + " ms \u00b7 " +
        esc(res.billed ? t("billed {cost}", { cost: usd(res.cost_usd) }) : t("no charge reported")) + "</p>" +
        "<p>" + esc(res.detail || "") + "</p>";
      if (res.transcript) lines += '<p class="mono">' + esc(t("Heard: \u201c{text}\u201d", { text: res.transcript })) + "</p>";
      h += callout(kind, lines);
    }
    h += "</div>";
    return h;
  }

  // Memory list
  function memoryBlock() {
    if (!S.memory) return '<p class="muted small">' + esc(t("Loading memory\u2026")) + '</p>';
    var items = S.memory.items || [];
    if (!items.length) return '<p class="empty">' + esc(t("Nothing remembered yet. Things you ask the assistant to remember will show up here.")) + '</p>';
    return '<div class="list">' + items.map(function (it) {
      return '<div class="list-row"><span class="l-badge">' + esc(it.kind || t("note")) + "</span>" +
        '<span class="l-txt"><span class="l-name">' + esc(it.text) + "</span>" +
        '<span class="l-path">' + esc(it.source || "") + " \u00b7 " + esc(it.created_at || "") + "</span></span>" +
        '<button type="button" class="btn sm ghost" data-act="forget" data-id="' + esc(it.id) + '" aria-label="' + esc(t("Forget this item")) + '">' +
        icon("trash") + "</button></div>";
    }).join("") + "</div>";
  }

  // Usage meters
  function usageBlock(scope) {
    if (!S.usage) {
      return card({ eyebrow: t("Budgets & usage"), title: t("Loading usage…"), body: '<div class="meter"><i></i></div>' });
    }
    var today = S.usage.today || {}, month = S.usage.month || {};
    function meter(title, u, sub) {
      var total = num(u.total_usd, 0), cap = num(u.cap_usd, 0);
      var p = pct(total, cap);
      var cls = cap > 0 && total >= cap ? "over" : p >= 80 ? "warn" : "";
      var left = Math.max(0, cap - total);
      return '<div class="stack">' +
        '<div class="meter-head"><span class="m-big">' + esc(usd(total)) + "</span>" +
        '<span class="m-sub">' + esc(t("{title} \u00b7 cap {cap}", { title: title, cap: cap > 0 ? usd(cap) : t("not set") })) +
        ' <span class="tag-est">' + esc(t("estimate")) + "</span></span></div>" +
        '<div class="meter ' + cls + '"><i style="width:' + p.toFixed(1) + '%"></i></div>' +
        '<div class="meter-foot"><span>' + esc(sub) + "</span><span>" +
        esc(cap > 0 ? t("{amount} left", { amount: usd(left) }) : t("no cap")) + "</span></div></div>";
    }
    function breakdown(u) {
      var backend = num(u.backend_usd, 0), tools = num(u.tools_usd, 0);
      return '<table class="usage"><caption class="sr-only">' + esc(t("Estimated spend breakdown")) + '</caption><thead><tr>' +
        '<th scope="col">' + esc(t("Line item")) + '</th><th scope="col" class="num">' + esc(t("Amount")) + '</th></tr></thead><tbody>' +
        "<tr><td>" + esc(t("Live session duration")) + "</td><td class=\"num\">" + esc(fmtDur(u.live_seconds)) + "</td></tr>" +
        "<tr><td>" + esc(t("Transcription audio")) + "</td><td class=\"num\">" + esc(fmtDur(u.transcribe_seconds)) + "</td></tr>" +
        '<tr><td>' + esc(t("Backend tokens & tools")) + '</td><td class="num">' + esc(usd(backend + tools)) + "</td></tr>" +
        '<tr><td class="lbl">' + esc(t("of which tool calls")) + '</td><td class="num">' + esc(usd(tools)) + "</td></tr>" +
        '<tr><td><b>' + esc(t("Estimated total")) + '</b></td><td class="num"><b>' + esc(usd(u.total_usd)) + "</b></td></tr>" +
        "</tbody></table>";
    }
    var events = (S.usage.events || []).slice(0, 6);
    var evHtml = events.length
      ? '<table class="usage"><thead><tr><th scope="col">' + esc(t("Recent activity")) + '</th><th scope="col">' + esc(t("When")) + '</th><th scope="col" class="num">' + esc(t("Est.")) + '</th></tr></thead><tbody>' +
        events.map(function (e) {
          var label = typeof e === "string" ? e : (e.label || e.kind || e.event || t("activity"));
          var when = typeof e === "string" ? "" : (e.when || e.at || e.time || "");
          var cost = typeof e === "string" ? null : (e.cost_usd !== undefined ? e.cost_usd : e.usd);
          return "<tr><td>" + esc(label) + '</td><td class="lbl">' + esc(when) + '</td><td class="num">' +
            (cost === null || cost === undefined ? "\u2014" : esc(usd(cost))) + "</td></tr>";
        }).join("") + "</tbody></table>"
      : '<p class="empty">' + esc(t("No activity recorded in this window yet.")) + '</p>';

    return card({
      eyebrow: t("Budgets & usage"),
      title: t("Spend against your caps"),
      desc: t("Every figure below is an estimate derived from the app's own metering, not a provider invoice."),
      cls: "glow",
      body: '<div class="usage-grid">' +
        '<div class="stack">' + meter(t("Today"), today, t("resets at midnight local")) +
        breakdown(today) + "</div>" +
        '<div class="stack">' + meter(t("This month"), month, t("resets on the 1st")) +
        breakdown(month) + "</div>" +
        "</div>",
      foot: '<span class="spacer"></span><button type="button" class="btn sm ghost" data-act="usage-refresh">' +
        icon("refresh") + " " + esc(t("Refresh")) + "</button>"
    }) + card({ eyebrow: t("Activity"), title: t("Recent events"), body: evHtml, cls: "" });
  }

  function budgetInputs(scope) {
    return row(t("Daily cap"), numHtml("daily_budget_usd", val("daily_budget_usd", 1), { min: 0, max: 500, step: 0.5, pre: "$", label: t("Daily budget in US dollars") }),
        t("Estimated spend allowed per day. 0 disables the cap."), "n_daily_budget_usd") +
      row("Monthly cap", numHtml("monthly_budget_usd", val("monthly_budget_usd", 20), { min: 0, max: 5000, step: 1, pre: "$", label: t("Monthly budget in US dollars") }),
        t("Estimated spend allowed per calendar month."), "n_monthly_budget_usd") +
      row(t("Longest single task"), numHtml("max_task_seconds", val("max_task_seconds", 300), { min: 15, max: 3600, step: 15, suf: t("seconds"), label: t("Maximum task length in seconds") }),
        t("Hard stop for one assistant task.")) +
      row(t("Tool calls per task"), numHtml("max_task_tool_calls", val("max_task_tool_calls", 20), { min: 1, max: 200, step: 1, label: t("Maximum tool calls per task") }),
        t("A ceiling on how far a single task can roam."));
  }

  function devicesBlock(scope) {
    var dev = S.devices;
    if (!dev) return '<p class="muted small">' + esc(t("Loading audio devices\u2026")) + '</p>';
    function opts(list, selected) {
      var h = "";
      (list || []).forEach(function (d) {
        var label = d.name + (d.channels ? " \u00b7 " + d.channels + "ch" : "") + (d.default ? " " + t("(system default)") : "");
        h += '<option value="' + esc(d.name) + '"' + (selected === d.name ? " selected" : "") + ">" + esc(label) + "</option>";
      });
      if (!h) h = '<option value="">' + esc(t("No devices found")) + "</option>";
      return h;
    }
    return '<div class="stack">' +
      row("Microphone", '<select class="select" data-act="select" data-key="input_device" id="s_input_device" aria-label="Input device">' +
        opts(dev.input, val("input_device", "")) + "</select>", t("Used for dictation and wake-word listening."), "s_input_device") +
      row(t("Speaker"), '<select class="select" data-act="select" data-key="output_device" id="s_output_device" aria-label="Output device">' +
        opts(dev.output, val("output_device", "")) + "</select>", t("Used for confirmation sounds and spoken replies."), "s_output_device") +
      '<div class="sep"></div>' +
      '<div class="eyebrow">' + esc(t("Microphone level test")) + '</div>' +
      '<div class="meter" id="micMeter" role="img" aria-label="Live microphone level"><i style="width:0%"></i></div>' +
      '<div class="inline"><button type="button" class="btn" data-act="mic-test">' +
      (S.micTesting ? icon("stop") + " " + esc(t("Stop test")) : icon("play") + " " + esc(t("Start level test"))) + "</button>" +
      '<span class="small muted mono" id="micVal">' + esc(t("level")) + " \u2014</span></div>" +
      '<p class="hint">' + esc(t("Say a sentence while the test runs. The bars should move with your voice, not stay pinned at either end.")) + '</p>' +
      "</div>";
  }

  var BINDINGS = [
    { k: "mouse_dictate", t: t("Dictate \u2014 mouse button"), h: t("Hold a mouse button to dictate while it is held.") },
    { k: "key_dictate_toggle", t: t("Dictate \u2014 keyboard toggle"), h: t("Tap once to start dictating, tap again to stop.") },
    { k: "key_assistant", t: t("Ask the assistant"), h: t("Hold to talk to the assistant instead of plain dictation.") },
    { k: "key_emergency_stop", t: t("Emergency stop"), h: t("Instantly stops dictation, audio capture and running tools.") }
  ];

  function bindingOf(which) {
    var c = cfg();
    if (c.shortcuts && typeof c.shortcuts === "object" && c.shortcuts[which]) return c.shortcuts[which];
    if (c[which]) return c[which];
    return null;
  }
  function bindingPatch(which, display) {
    var c = cfg();
    if (c.shortcuts && typeof c.shortcuts === "object") {
      var next = {};
      for (var k in c.shortcuts) if (Object.prototype.hasOwnProperty.call(c.shortcuts, k)) next[k] = c.shortcuts[k];
      next[which] = display;
      return { shortcuts: next };
    }
    var p = {};
    p[which] = display;
    return p;
  }
  function shortcutsBlock() {
    return '<div class="keys">' + BINDINGS.map(function (b) {
      var v = bindingOf(b.k);
      var disp = v ? (typeof v === "string" ? v : v.display || "") : "";
      var conflict = v && typeof v === "object" ? v.conflict : null;
      return '<div class="key-row">' +
        '<span><span class="k-t">' + esc(b.t) + '</span><p class="k-h">' + esc(b.h) + "</p>" +
        (conflict ? '<p class="conflict">' + esc(t("In use by another app: {name}", { name: conflict })) + "</p>" : "") + "</span>" +
        "<kbd class=\"combo" + (disp ? "" : " none") + "\">" + esc(disp || t("not set")) + "</kbd>" +
        '<button type="button" class="btn sm" data-act="record" data-which="' + esc(b.k) + '">' +
        icon("keytool") + (disp ? t("Change") : t("Set")) + "</button></div>";
    }).join("") + "</div>";
  }

  function loadWakeModel() {
    if (S.wakeModel !== undefined || S.wakeFetching) return;
    S.wakeFetching = true;
    call("wake_model_status").then(function (m) {
      S.wakeFetching = false;
      S.wakeModel = m || { available: false, detail: "" };
      render();
    });
  }

  function wakeModelBlock() {
    var m = S.wakeModel;
    if (!m) return "";
    if (!m.available) {
      // The worst state this app can be in: the wake word is switched on, the
      // phrase does nothing at all, and nothing tells you why. Say it here.
      return '<div class="row wide" style="display:block">' +
        '<div class="callout warn"><div class="co-body">' +
        '<strong>' + esc(t("The wake-word model is not downloaded yet.")) + '</strong>' +
        '<p class="hint">' + esc(t("Until it is, saying the phrase does nothing. It is about 20 MB, fetched once, and it then runs entirely on your machine \u2014 no audio leaves your PC while it listens.")) + '</p>' +
        (S.wakeBusy ? '<p class="hint">' + esc(t("Downloading\u2026")) + '</p>' : "") +
        '</div></div>' +
        '<div class="inline" style="margin-top:10px">' +
        (S.wakeBusy
          ? '<button type="button" class="btn" disabled>' + esc(t("Downloading\u2026")) + '</button>'
          : '<button type="button" class="btn primary" data-act="wake-download">' + icon("wake-setup") + " " + esc(t("Download the model")) + '</button>') +
        '<span class="small muted">' + esc(t("One-time download from the sherpa-onnx releases.")) + '</span>' +
        '</div></div>';
    }
    return '<div class="row wide" style="display:block">' +
      '<div class="callout ok"><div class="co-body">' +
      '<strong>' + esc(t("Wake-word model installed and ready.")) + '</strong>' +
      '</div></div></div>';
  }

  function wakeBlock() {
    var phrases = val("wake_phrases", []) || [];
    var thr = num(val("wake_threshold", 0.55), 0.55);
    return wakeModelBlock() +
      toggle("wake_enabled", !!val("wake_enabled", true), t("Listen for a wake word"),
        t("Keeps a small, local listener running so you can start hands-free.")) +
      '<div class="row wide"><div class="row-label"><label>' + esc(t("Wake phrases")) + '</label>' +
      '<p class="hint">' + esc(t("Short phrases the local listener watches for. Keep them two or three syllables.")) + '</p></div>' +
      '<div class="row-ctl" style="justify-content:flex-start;display:block">' +
      chipsHtml("wake_phrases", phrases, { teal: true, placeholder: t("e.g. hey jarvis"), aria: t("Add a wake phrase"), addLabel: t("Add phrase") }) +
      "</div></div>" +
      '<div class="row wide"><div class="row-label"><label for="r_wake_threshold">' + esc(t("Wake sensitivity")) + '</label>' +
      '<p class="hint">' + esc(t("Lower catches quieter speech but fires more often by accident.")) + '</p></div>' +
      '<div class="row-ctl" style="display:block">' +
      slider("wake_threshold", thr, {
        // The floor MUST include the shipped default (0.22). It used to start at
        // 0.30, so the stored value was below the slider's range: merely opening
        // this page and saving pushed the threshold UP, which makes the wake word
        // LESS sensitive - the opposite of what someone debugging a wake word
        // that never fires wants.
        min: 0.05, max: 0.90, step: 0.01, label: t("Wake-word sensitivity threshold"),
        leftLabel: t("More sensitive"), lowLabel: "0.05", highLabel: "0.90", rightLabel: t("Fewer false triggers"),
        display: function (v) { return v.toFixed(2); }
      }) + "</div></div>" +
      '<div class="sep"></div>' +
      '<div class="inline"><button type="button" class="btn" data-act="wake-setup">' + icon("refresh") + " " + esc(t("Run wake-word setup")) + "</button>" +
      '<span class="small muted">' + esc(t("Teaches the local model your voice and checks the microphone gain.")) + '</span></div>';
  }

  /* ── 7. Settings sections ──────────────────────────────────────────── */
  var SECTIONS = [
    { id: "general", label: t("General"), ic: "sliders", render: secGeneral },
    { id: "dictation", label: t("Dictation"), ic: "mic", render: secDictation },
    { id: "assistant", label: t("Assistant"), ic: "spark", render: secAssistant },
    { id: "shortcuts", label: t("Shortcuts"), ic: "key", render: secShortcuts },
    { id: "wake", label: t("Wake word"), ic: "wave", render: secWake },
    { id: "vocab", label: t("Vocabulary"), ic: "tag", render: secVocab },
    { id: "memory", label: t("Memory"), ic: "brain", render: secMemory },
    { id: "history", label: t("History"), ic: "clock", render: secHistory },
    { id: "budgets", label: t("Budgets & usage"), ic: "gauge", render: secBudgets },
    { id: "integrations", label: t("Integrations"), ic: "plug", render: secIntegrations },
    { id: "privacy", label: t("Privacy"), ic: "shield", render: secPrivacy },
    { id: "about", label: t("About"), ic: "info", render: secAbout }
  ];

  function secGeneral() {
    return pageHead(t("General"), t("How Jarvis looks, behaves at idle, and starts with Windows.")) +
      card({
        eyebrow: t("Appearance"), title: t("Look and motion"),
        body:
          row(t("Language"), langSelect(), t("Changes the language of this window. It applies immediately.")) +
          row(t("Theme"), seg("theme", val("theme", "dark"), [
            { v: "dark", label: t("Dark"), hint: t("Default low-light palette") },
            { v: "light", label: t("Light"), hint: t("High-contrast daylight") }
          ]), t("Applies immediately across the window.")) +
          toggle("reduced_motion", !!val("reduced_motion", false), t("Reduce motion"),
            t("Stops the waveform, orb and screen transitions from animating."))
      }) +
      card({
        eyebrow: t("Behaviour"), title: t("Idle and startup"),
        body:
          row(t("Idle timeout"), numHtml("idle_timeout_seconds", val("idle_timeout_seconds", 900), { min: 30, max: 7200, step: 30, suf: t("seconds"), label: t("Idle timeout in seconds") }),
            t("After this much silence the app releases the microphone and the wake listener.")) +
          toggle("start_at_login", !!val("start_at_login", true), t("Start at login"),
            t("Launches Jarvis into the tray when you sign in to Windows.")) +
          '<div class="row"><div class="row-label"><label>' + esc(t("Version")) + '</label><p class="hint">' + esc(t("Installed build.")) + '</p></div>' +
          '<div class="row-ctl"><span class="mono muted">' + esc((S.state && S.state.version) || "\u2014") + "</span></div></div>"
      });
  }

  function secDictation() {
    var engine = val("dictation_engine", "live");
    return pageHead(t("Dictation"), t("How speech becomes text in whatever app has focus.")) +
      commandsCard() +
      card({
        eyebrow: t("Engine"), title: t("Where the words come from"), cls: "glow",
        body: seg("dictation_engine", engine, [
          { v: "live", label: t("Live-first"), hint: t("Streams as you speak \u2014 fastest, highest cost") },
          { v: "economy", label: t("Economy dictation"), hint: t("Records, then transcribes in one batch \u2014 slower, cheaper") }
        ]) +
        '<div class="mt12"></div>' +
        callout("info", "<p>Live-first feels instant and is billed by the second. Economy dictation buffers your voice, sends one request and costs noticeably less per minute.</p>")
      }) +
      card({
        eyebrow: t("Cleanup"), title: t("Tidying the transcript"),
        body: toggle("cleanup_enabled", !!val("cleanup_enabled", true), t("Run transcript cleanup"),
            t("Sends the raw transcript through a short pass that fixes punctuation and fillers.")) +
          row(t("Cleanup style"), seg("cleanup_style", val("cleanup_style", "light"), [
            { v: "verbatim", label: t("Verbatim"), hint: t("Exactly what you said") },
            { v: "light", label: t("Light"), hint: t("Punctuation and fillers") },
            { v: "polish", label: t("Polish"), hint: t("Rewrites for clarity") }
          ]), t("Verbatim is safest for commands and code."))
      }) +
      card({
        eyebrow: t("Context"), title: t("Context-aware dictation"),
        body: toggle("contextual_dictation", !!val("contextual_dictation", false), t("Adapt to the focused app"),
          t("Uses the window title and field type to bias spelling \u2014 code gets code formatting, email gets prose.")) +
          '<div class="sep"></div>' +
          '<div class="inline"><button type="button" class="btn" data-act="test-insert">' + icon("play") +
          " " + esc(t("Test insert text")) + '</button><span class="small muted" id="insertResult">' +
          esc(t("Writes a harmless sample string into the focused field so you can confirm insertion works.")) + "</span></div>"
      });
  }

  function secAssistantAnchor() { return null; }

  function secAssistant() {
    var apps = val("approved_apps", []) || [];
    var paths = val("approved_paths", []) || [];
    var urls = val("approved_urls", []) || [];
    return pageHead(t("Assistant"), t("What the assistant may do, for how long, and where it may reach.")) +
      voiceCard() +
      card({
        eyebrow: t("Limits"), title: t("Task ceilings"),
        body:
          row(t("Longest single task"), numHtml("max_task_seconds", val("max_task_seconds", 300), { min: 15, max: 3600, step: 15, suf: t("seconds"), label: t("Maximum task length") }),
            t("A task is stopped automatically when it runs longer than this.")) +
          row(t("Tool calls per task"), numHtml("max_task_tool_calls", val("max_task_tool_calls", 20), { min: 1, max: 200, step: 1, label: t("Maximum tool calls") }),
            t("Caps how many actions one spoken request can trigger."))
      }) +
      card({
        eyebrow: t("Approved folders"), title: t("Where files may be touched"),
        desc: t("The assistant can only read and write inside these folders."),
        body: listHtml("approved_paths", paths, {
          badge: t("folder"), empty: t("No folders approved. The assistant cannot touch the filesystem."),
          actions: '<button type="button" class="btn sm" data-act="pick-folder">' + icon("folder") + " " + esc(t("Add folder\u2026")) + "</button>"
        })
      }) +
      card({
        eyebrow: t("Approved apps"), title: t("Apps it may open or drive"),
        body: listHtml("approved_apps", apps, {
          badge: t("app"), empty: t("No apps approved."),
          actions: '<button type="button" class="btn sm" data-act="pick-app">' + icon("app") + " " + esc(t("Add app\u2026")) + "</button>"
        })
      }) +
      card({
        eyebrow: t("Web access"), title: t("Allowed sites"),
        desc: t("Pages the assistant may fetch or summarise. Anything else is refused."),
        body: chipsHtml("approved_urls", urls, { teal: true, placeholder: "https://example.com/docs", aria: t("Add an allowed URL"), addLabel: t("Allow") })
      });
  }

  /* ── Voice picker ─────────────────────────────────────────────────────
     The roster comes from the API docs' own "Voice options" table via
     bridge.get_voices(); nothing here is invented. The docs are explicit that
     a session's voice cannot be changed once it has started, so the card says
     when a change takes effect rather than implying it is instant.          */
  function voiceCard() {
    var v = S.voices;
    if (!v) return card({ eyebrow: t("Voice"), title: t("Assistant voice"),
      body: '<p class="muted small">' + esc(t("Loading voices\u2026")) + '</p>' });
    var current = val("voice", v.current || v["default"]);
    var suggested = v.suggested || [];
    var list = (v.voices || []).slice().sort(function (a, b) {
      var ai = suggested.indexOf(a.id), bi = suggested.indexOf(b.id);
      if (ai >= 0 || bi >= 0) return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
      return a.label.localeCompare(b.label);
    });
    var opts = list.map(function (o) {
      var bits = [o.language];
      if (o.region && o.region !== "Default") bits.push(o.region);
      if (o.presentation) bits.push(o.presentation);
      return '<option value="' + esc(o.id) + '"' + (String(o.id) === String(current) ? " selected" : "") +
        ">" + esc(o.label) + " \u2014 " + esc(bits.join(", ")) + "</option>";
    }).join("");
    var chips = suggested.map(function (id) {
      var o = null, i;
      for (i = 0; i < list.length; i++) if (list[i].id === id) o = list[i];
      if (!o) return "";
      return '<button type="button" class="btn sm' + (String(id) === String(current) ? "" : " ghost") +
        '" data-act="pick-voice" data-val="' + esc(id) + '">' + esc(o.label) + "</button>";
    }).join(" ");
    return card({
      eyebrow: t("Voice"), title: t("Assistant voice"),
      desc: t("The voice the assistant speaks in. Dictation is unaffected."),
      body:
        row(t("Voice"), '<select class="inp" id="voice_sel" data-act="voice-select" aria-label="' + esc(t("Assistant voice")) + '">' +
          opts + "</select>",
          // row() escapes the hint itself - escaping it here too rendered
          // the apostrophe as a literal "&#39;" on screen.
          t("Applies to {when}.", { when: t(v.applies || t("the next session")) })) +
        '<div class="row"><div class="row-label"><label>' + esc(t("Australian voices")) + '</label>' +
        '<p class="hint">' + esc(t("Closest to how you speak.")) + '</p></div>' +
        '<div class="row-ctl">' + chips + "</div></div>"
    });
  }

  /* ── Spoken commands ──────────────────────────────────────────────────
     The exact verb list, straight from core/commands.describe_vocabulary().
     Showing the real phrases matters: a command layer whose vocabulary is a
     mystery is one you cannot trust or predict.                             */
  function commandsCard() {
    var c = S.commandHelp;
    var on = !!val("voice_commands_enabled", true);
    var body = toggle("voice_commands_enabled", on, t("Act on spoken commands"),
      t("When an utterance STARTS with one of the verbs below it is carried out instead of typed. Anything else is dictated as text."));
    if (c && c.groups) {
      body += '<ul class="cmd-list">' + c.groups.map(function (g) {
        return '<li><div class="cmd-verb">' + esc(g.verb) + "</div>" +
          '<div class="cmd-body"><p class="small">' + esc(g.does) + "</p>" +
          '<p class="mono small muted">' + esc(g.example) + "</p>" +
          '<p class="small muted">' + esc(t("Phrases: {list}", { list: (g.phrases || []).join(", ") })) + "</p></div></li>";
      }).join("") + "</ul>";
    }
    body += '<p class="small muted">' +
      esc(t("Destructive verbs are deliberately absent. Every action still goes through the same approval and allowlist checks as the assistant, and the emergency stop blocks all of them.")) + "</p>";
    return card({ eyebrow: t("Spoken commands"), title: t("Say it instead of typing it"), body: body });
  }

  function secShortcuts() {
    return pageHead(t("Shortcuts"), t("Global shortcuts that work from any app, including over full-screen windows.")) +
      card({
        eyebrow: t("Bindings"), title: t("Your shortcuts"),
        desc: t("Click Set and then press the key combination or mouse button you want."),
        body: shortcutsBlock()
      }) +
      card({
        eyebrow: t("Safety"), title: t("Emergency stop"),
        body: '<p class="small muted">The emergency stop cancels the current dictation, discards the buffer, and halts any running tools. ' +
          "Keep it on a key you can reach without looking.</p>"
      });
  }

  function secWake() {
    loadWakeModel();
    return pageHead(t("Wake word"), t("Hands-free starting and stopping, handled locally on your machine.")) +
      card({ eyebrow: t("Wake word"), title: t("Listener"), cls: "glow", body: wakeBlock() });
  }

  function secVocab() {
    var vocab = val("vocab", []) || [];
    if (S.state && S.state.vocab && !S.state.config.vocab) vocab = S.state.vocab;
    return pageHead(t("Vocabulary"), t("Names, jargon and spellings the recogniser keeps getting wrong.")) +
      card({
        eyebrow: t("Custom words"), title: t("Your vocabulary"),
        desc: t("Entries are biased toward during transcription. Add product names, people and acronyms."),
        body: chipsHtml("vocab", vocab, { placeholder: t("e.g. Kubernetes, Postgres, HERMES"), aria: t("Add a vocabulary word"), addLabel: t("Add word") }) +
          '<div class="sep"></div>' +
          '<p class="small muted">' + esc(t("Tip: keep entries short. Whole sentences belong in the assistant\'s memory, not the vocabulary.")) + "</p>"
      });
  }

  function secMemory() {
    return pageHead(t("Memory"), t("Facts the assistant has been asked to remember, and the transcript history.")) +
      card({ eyebrow: t("Stored facts"), title: t("What the assistant remembers"), body: memoryBlock(),
        foot: '<span class="spacer"></span><button type="button" class="btn sm ghost" data-act="memory-refresh">' + icon("refresh") + " " + esc(t("Refresh")) + "</button>" }) +
      card({
        eyebrow: t("History"), title: t("Transcript history"),
        body: '<p class="small muted">' + esc(t("Clearing history removes stored transcripts from this machine. Stored facts above are kept until you forget them individually.")) + '</p>',
        foot: '<button type="button" class="btn danger sm" data-act="clear-history">' + icon("trash") + " Clear transcript history</button>"
      });
  }

  /* ── History ──────────────────────────────────────────────────────────
     Everything the user has dictated, newest first, with a copy button on each
     row. The app already wrote every utterance to the `conversation` table;
     until now nothing could read it back, so a prompt that took a minute to
     say could not be recovered. Search filters client-side over text already
     loaded - no round trip per keystroke.                                   */
  function secHistory() {
    return pageHead(t("History"), t("Every prompt you have dictated, newest first. Copy one back out to reuse it.")) +
      card({
        eyebrow: t("Search"), title: t("Find a prompt"),
        body:
          '<div class="row"><div class="row-ctl" style="flex:1">' +
          '<input type="search" class="inp" id="hist_q" placeholder="\' + esc(t("Filter by any word\u2026")) + \'" ' +
          'value="' + esc(S.historyQuery || "") + '" data-act="history-query" aria-label="' + esc(t("Filter history")) + '">' +
          "</div></div>" + historyBlock(),
        foot: '<span class="spacer"></span>' +
          '<button type="button" class="btn sm ghost" data-act="history-refresh">' + icon("refresh") + " " + esc(t("Refresh")) + "</button>"
      }) +
      card({
        eyebrow: t("Retention"), title: t("How long this is kept"),
        body: '<p class="small muted">' +
          esc(t("Transcripts stay on this machine only, in")) + ' <code>%LOCALAPPDATA%\\Jarvis\\jarvis.db</code>' +
          esc(t(", and are pruned after the retention window set in Privacy. Nothing here is uploaded.")) + "</p>",
        foot: '<button type="button" class="btn danger sm" data-act="clear-history">' + icon("trash") + " Clear all history</button>"
      });
  }

  function historyBlock() {
    if (!S.history) return '<p class="muted small">' + esc(t("Loading history\u2026")) + '</p>';
    var items = S.history.items || [];
    var q = (S.historyQuery || "").trim().toLowerCase();
    if (q) {
      items = items.filter(function (it) {
        return String(it.text || "").toLowerCase().indexOf(q) >= 0;
      });
    }
    if (!items.length) {
      return '<p class="empty">' + (q
        ? esc(t("Nothing matches \u201c{query}\u201d.", { query: S.historyQuery }))
        : esc(t("Nothing dictated yet. Anything you say into a text box shows up here."))) + "</p>";
    }
    var rows = items.slice(0, 300).map(function (it) {
      var tags = "";
      if (it.command) tags += '<span class="tag">' + esc(it.command) + "</span>";
      if (it.engine) tags += '<span class="tag ghost">' + esc(it.engine) + "</span>";
      if (it.inserted) tags += '<span class="tag ghost">inserted</span>';
      return '<li class="hist-row">' +
        '<div class="hist-main">' +
        '<p class="hist-text">' + esc(it.text || "") + "</p>" +
        '<div class="hist-meta"><span class="mono small muted">' + esc(it.ts || "") + "</span> " +
        tags + '<span class="small muted">' + esc(t("{count} chars", { count: it.chars || 0 })) + "</span></div>" +
        "</div>" +
        '<button type="button" class="btn sm ghost" data-act="history-copy" data-id="' +
        esc(String(it.id)) + '" title="' + esc(t("Copy this prompt")) + '">' + icon("copy") + " " + esc(t("Copy")) + "</button>" +
        "</li>";
    }).join("");
    return '<ul class="hist-list">' + rows + "</ul>" +
      '<p class="small muted">' + esc(t("{shown} of {total} shown.", { shown: items.length, total: (S.history.items || []).length })) + "</p>";
  }

  function secBudgets() {
    return pageHead(t("Budgets & usage"), t("Estimated spend, hard caps, and where the money goes.")) +
      usageBlock("settings") +
      card({ eyebrow: t("Caps"), title: t("Spending limits"), cls: "glow", body: budgetInputs("settings") }) +
      card({ eyebrow: t("Comparison"), title: t("Optional accuracy comparison"),
        body: '<p class="small muted">' + esc(t("Runs the same audio through the live and economy paths and shows how the transcripts differ, plus the real cost of each. It uses your microphone and is billable.")) + '</p>' +
          calibrationBlock() });
  }

  function calibrationBlock() {
    var c = S.calib;
    if (!c) {
      return '<div class="inline"><button type="button" class="btn" data-act="calibrate">' + icon("play") +
        " " + esc(t("Start accuracy comparison")) + '</button><span class="small muted">' + esc(t("Opt in \u2014 nothing runs until you press it.")) + "</span></div>";
    }
    var done = num(c.index, 0), total = Math.max(1, num(c.total, 1));
    var p = (done / total) * 100;
    return '<div class="stack"><div class="progress"><i style="width:' + p.toFixed(1) + '%"></i></div>' +
      '<p class="phase-line">' + esc(t("Sample {n} of {total} \u00b7 {phase}", { n: done, total: total, phase: c.phase || t("working") })) +
      (c.text ? ' \u00b7 \u201c' + esc(c.text) + "\u201d" : "") + "</p>" +
      '<p class="small muted">' + esc(t("Keep talking normally. Results appear here when the run finishes.")) + "</p></div>";
  }

  function secIntegrations() {
    return pageHead(t("Integrations"), t("Local coding agents Jarvis can hand long jobs to.")) +
      card({ eyebrow: t("Local agents"), title: t("Detected CLIs"), body: agentsBlock(),
        foot: '<span class="spacer"></span><button type="button" class="btn sm ghost" data-act="agents-refresh">' + icon("refresh") + " " + esc(t("Re-check")) + "</button>" }) +
      card({ eyebrow: t("Speech service"), title: t("Connection tests"), desc: t("Confirm the API key and network path work before you rely on dictation."), body: connTestBlock("settings") });
  }

  function agentsBlock() {
    var a = S.agents;
    if (!a) return '<p class="muted small">' + esc(t("Checking for Codex and Claude\u2026")) + '</p>';
    function one(key, label) {
      var d = a[key] || {};
      var installed = !!d.installed;
      var cls = !installed ? "bad" : d.authenticated ? "ok" : "warn";
      var status = !installed ? t("Not installed") : d.authenticated ? t("Ready") : t("Installed, not signed in");
      return '<div class="agent"><div class="agent-top"><span class="dot ' + cls + '" aria-hidden="true"></span>' +
        '<span class="agent-name">' + esc(label) + '</span><span class="small muted">' + esc(status) + "</span></div>" +
        '<div class="agent-meta">' +
        '<div class="am"><b>' + esc(t("Version")) + '</b><span>' + esc(d.version || "\u2014") + "</span></div>" +
        '<div class="am"><b>Billing</b><span>' + esc(d.billing_mode || "\u2014") + "</span></div>" +
        '<div class="am"><b>' + esc(t("Work folder")) + '</b><span class="mono">' + esc(d.workdir || "\u2014") + "</span></div>" +
        "</div></div>";
    }
    return '<div class="agent-grid">' + one("codex", t("OpenAI Codex CLI")) + one("claude", t("Claude Code CLI")) + "</div>";
  }

  function secPrivacy() {
    return pageHead(t("Privacy"), t("What is kept on this machine, what leaves it, and what you can remove.")) +
      card({
        eyebrow: t("Data on disk"), title: t("Retention"),
        body:
          toggle("store_raw_audio", !!val("store_raw_audio", false), t("Keep raw audio"),
            t("Off by default. When on, the audio behind each transcript is written to disk so you can replay it.")) +
          toggle("telemetry", !!val("telemetry", false), t("Send anonymous diagnostics"),
            t("Off by default. Sends crash reports and feature counters only \u2014 never transcripts or audio."))
      }) +
      card({
        eyebrow: t("Credentials"), title: t("API key"),
        body: fingerprintBlock()
      }) +
      card({
        eyebrow: t("Removal"), title: t("Erase local data"),
        body: '<p class="small muted">' + esc(t("Clears stored transcripts and remembered facts from this machine. Settings and your API key are kept.")) + '</p>',
        foot: '<button type="button" class="btn danger sm" data-act="clear-history">' + icon("trash") + " Clear transcript history</button>" +
          '<span class="spacer"></span><button type="button" class="btn ghost sm" data-act="clear-key">' + esc(t("Remove API key")) + '</button>'
      });
  }

  function secAbout() {
    var st = S.state || {};
    return pageHead(t("About"), "Jarvis") +
      card({
        eyebrow: t("Build"), title: "Jarvis", cls: "glow",
        body: '<div class="inline">' + wave(11, "wave-live active") + '<span class="mono muted">v' + esc(st.version || "\u2014") + "</span></div>" +
          '<dl class="kv mt12">' +
          "<dt>" + esc(t("Version")) + "</dt><dd>" + esc(st.version || "\u2014") + "</dd>" +
          "<dt>" + esc(t("Setup state")) + "</dt><dd>" + esc(st.setup_complete ? t("Setup complete") : t("Setup not finished")) + "</dd>" +
          "<dt>" + esc(t("API key")) + "</dt><dd>" + esc(st.key_fingerprint || t("not set")) + "</dd>" +
          "<dt>" + esc(t("Dictation engine")) + "</dt><dd>" + esc(val("dictation_engine", "live")) + "</dd>" +
          "</dl>"
      }) +
      card({
        eyebrow: t("Maintenance"), title: t("Re-run setup or quit"),
        body: '<p class="small muted">' + esc(t("Re-running setup walks through the wizard again without clearing your settings. Quitting closes the tray app and stops the wake listener.")) + '</p>',
        foot: '<button type="button" class="btn" data-act="wizard-restart">' + esc(t("Re-run setup")) + '</button>' +
          '<span class="spacer"></span><button type="button" class="btn danger" data-act="quit">' + esc(t("Quit Jarvis")) + '</button>'
      });
  }

  /* ── 8. Setup wizard steps ─────────────────────────────────────────── */
  var WIZARD = [
    {
      id: "welcome", label: t("Welcome"), sub: t("Get started with Jarvis"), title: t("Welcome to Jarvis"), eyebrow: t("Setup your Jarvis"),
      para: t("Press a shortcut, speak naturally, and your words land in whatever app has focus. This quick setup gets your audio, shortcuts, and preferences just right. You can change everything later in Settings."),
      body: function () {
        return card({
          eyebrow: t("What you'll set up"), title: t("Five minutes, twelve short steps"),
          cls: "glow welcome",
          desc: t("A smoother, faster way to work \u2014 with your voice."),
          badge: '<span class="chip">' + icon("clock") + " " + esc(t("~ 5 min")) + "</span>",
          body:
            '<div class="mic-strip" aria-hidden="true">' +
            wave(52, "wave-strip") +
            '<span class="mic-dot">' +
            '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" ' +
            'stroke-width="1.8" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"></rect>' +
            '<path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3"></path></svg></span>' +
            wave(52, "wave-strip") +
            "</div>" +
            '<ul class="feats">' +
            feat("bolt", t("Natural conversation"), t("Speak, don\u2019t click.")) +
            feat("layers", t("Works in your apps"), t("Stays in the background.")) +
            feat("lock", t("Your data, your control"), t("Private and secure.")) +
            "</ul>",
          foot: '<span class="note">' + icon("info") +
            "<span>" + esc(t("You\u2019ll need an API key for the speech service. Keep it nearby \u2014 step two asks for it.")) + "<br>" +
            esc(t("Nothing is sent anywhere until you finish and start dictating, except the connection tests you choose to run.")) + "</span></span>"
        });
      }
    },
    {
      id: "apikey", label: t("API key"), sub: t("Connect your speech service"), title: t("Add your API key"), eyebrow: t("Step 2 \u00b7 Credentials"),
      para: t("Jarvis talks to the speech service with your key. It is stored in Windows Credential Manager and never echoed back into this window."),
      body: function () {
        return card({ eyebrow: t("Credentials"), title: t("Speech service key"), cls: "glow", body: keyForm() + '<div class="sep"></div>' + fingerprintBlock() }) +
          card({ eyebrow: t("Verify"), title: t("Test the connection"), desc: t("Confirm the key works and your network can reach the service."), body: connTestBlock("wizard") });
      }
    },
    {
      id: "audio", label: t("Mic & speaker"), sub: t("Set up your audio devices"), title: t("Check your microphone"), eyebrow: t("Step 3 \u00b7 Audio"),
      para: t("Pick the microphone you actually speak into, then watch the level meter while you talk. A good input peaks around two thirds of the bar without clipping."),
      body: function () { return card({ eyebrow: t("Audio devices"), title: t("Input and output"), body: devicesBlock("wizard") }); }
    },
    {
      id: "shortcuts", label: t("Shortcuts"), sub: t("Talk to Jarvis your way"), title: t("Choose your shortcuts"), eyebrow: t("Step 4 \u00b7 Shortcuts"),
      para: t("These wake the app from anywhere in Windows. The defaults suit most people \u2014 change anything you like, and keep one hand free for the emergency stop."),
      body: function () { return card({ eyebrow: t("Bindings"), title: t("Global shortcuts"), body: shortcutsBlock() }); }
    },
    {
      id: "wake", label: t("Wake word"), sub: t("Choose how to activate"), title: t("Hands-free wake word"), eyebrow: t("Step 5 \u00b7 Wake word"),
      para: t("A small local model listens for your wake phrase so you can start talking without touching the keyboard. Nothing is uploaded while it listens."),
      body: function () { return card({ eyebrow: t("Wake word"), title: t("Listener"), body: wakeBlock() }); }
    },
    {
      id: "style", label: t("Style & vocab"), sub: t("Make it sound like you"), title: t("Style and vocabulary"), eyebrow: t("Step 6 \u00b7 Output"),
      para: t("Decide how tidy the transcript should be, and teach the recogniser the names it will keep hearing."),
      body: function () {
        var vocab = val("vocab", []) || [];
        return card({
          eyebrow: t("Cleanup"), title: t("Transcript style"),
          body: toggle("cleanup_enabled", !!val("cleanup_enabled", true), t("Run transcript cleanup"), t("Fixes punctuation and removes filler words.")) +
            row(t("Style"), seg("cleanup_style", val("cleanup_style", "light"), [
              { v: "verbatim", label: t("Verbatim") }, { v: "light", label: t("Light") }, { v: "polish", label: t("Polish") }
            ]), null)
        }) + card({
          eyebrow: t("Vocabulary"), title: t("Words to get right"),
          body: chipsHtml("vocab", vocab, { placeholder: t("e.g. Kubernetes, Postgres"), aria: t("Add a vocabulary word"), addLabel: t("Add word") })
        });
      }
    },
    {
      id: "accuracy", label: t("Accuracy check"), sub: t("Test and calibrate"), title: t("Optional accuracy comparison"), eyebrow: t("Step 7 \u00b7 Optional"),
      para: t("Live-first dictation and economy dictation make different trade-offs. Run a short comparison to hear and price both, or skip it and decide later."),
      body: function () {
        return card({ eyebrow: t("Comparison"), title: t("Live vs economy"), body: calibrationBlock() }) +
          card({ eyebrow: t("Cost"), title: t("What it costs"), body: connTestBlock("wizard") });
      }
    },
    {
      id: "budgets", label: t("Budgets"), sub: t("Set usage limits"), title: t("Set your limits"), eyebrow: t("Step 8 \u00b7 Budgets"),
      para: t("Jarvis counts what it spends and stops when a cap is reached. Set a low cap for the first week and raise it once you know your habits."),
      body: function () {
        return card({ eyebrow: t("Caps"), title: t("Budget and task limits"), cls: "glow", body: budgetInputs("wizard") }) +
          card({ eyebrow: t("Live totals"), title: t("Where you stand"), body: usageBlock("wizard") });
      }
    },
    {
      id: "apps", label: t("Apps & folders"), sub: t("Give Jarvis context"), title: t("Approved apps and folders"), eyebrow: t("Step 9 \u00b7 Access"),
      para: t("The assistant can only touch what you approve here. Nothing is approved by default, so it will ask before reaching outside a folder you have added."),
      body: function () {
        var paths = val("approved_paths", []) || [];
        return card({
          eyebrow: t("Folders"), title: t("Approved folders"),
          body: listHtml("approved_paths", paths, {
            badge: t("folder"), empty: t("No folders approved yet."),
            actions: '<button type="button" class="btn sm" data-act="pick-folder">' + icon("folder") + " " + esc(t("Add folder\u2026")) + "</button>"
          })
        }) + card({
          eyebrow: t("Apps"), title: t("Approved apps"),
          body: listHtml("approved_apps", val("approved_apps", []) || [], {
            badge: t("app"), empty: t("No apps approved yet."),
            actions: '<button type="button" class="btn sm" data-act="pick-app">' + icon("app") + " " + esc(t("Add app\u2026")) + "</button>"
          })
        });
      }
    },
    {
      id: "agents", label: t("Agent status"), sub: t("Check system health"), title: t("Coding agents"), eyebrow: t("Step 10 \u00b7 Agents"),
      para: t("If you have Codex or Claude Code installed, Jarvis can hand long jobs to them. Both are optional \u2014 dictation works without either."),
      body: function () {
        return card({ eyebrow: t("Detected"), title: t("Local agent CLIs"), body: agentsBlock(),
          foot: '<span class="spacer"></span><button type="button" class="btn sm ghost" data-act="agents-refresh">' + icon("refresh") + " " + esc(t("Re-check")) + "</button>" });
      }
    },
    {
      id: "login", label: t("Start at login"), sub: t("Launch with Windows"), title: t("Start with Windows"), eyebrow: t("Step 11 \u00b7 Startup"),
      para: t("Jarvis lives in the tray. Starting at login means your shortcuts work from the moment you sign in."),
      body: function () {
        return card({
          eyebrow: t("Startup"), title: t("Launch at sign-in"), cls: "glow",
          body: toggle("start_at_login", !!val("start_at_login", true), t("Start Jarvis at login"),
            t("Adds a tray entry when you sign in to Windows. You can turn this off again at any time.")) +
            '<div class="sep"></div>' +
            '<p class="small muted">' + esc(t("The app starts minimised, with the wake listener off until it has finished loading.")) + '</p>'
        });
      }
    },
    {
      id: "finish", label: t("Finish"), sub: t("You're all set"), title: t("You're ready"), eyebrow: t("Step 12 \u00b7 Finish"),
      para: t("Everything is set. Press Finish to save and start using Jarvis from the tray."),
      body: function () {
        var st = S.state || {};
        return card({
          eyebrow: t("Summary"), title: t("Your setup"), cls: "glow",
          body: '<dl class="kv">' +
            "<dt>" + esc(t("API key")) + "</dt><dd>" + esc(st.key_fingerprint || t("not set \u2014 dictation will not work")) + "</dd>" +
            "<dt>" + esc(t("Dictation")) + "</dt><dd>" + esc(val("dictation_engine", "live") === "economy" ? t("Economy dictation") : t("Live-first")) + "</dd>" +
            "<dt>" + esc(t("Cleanup")) + "</dt><dd>" + esc(val("cleanup_enabled", true) ? val("cleanup_style", "light") : t("off")) + "</dd>" +
            "<dt>" + esc(t("Wake word")) + "</dt><dd>" + esc(val("wake_enabled", true) ? ((val("wake_phrases", []) || []).join(", ") || t("on, no phrases set")) : t("off")) + "</dd>" +
            "<dt>" + esc(t("Daily cap")) + "</dt><dd>" + esc(usd(val("daily_budget_usd", 0))) + "</dd>" +
            "<dt>" + esc(t("Folders approved")) + "</dt><dd>" + esc((val("approved_paths", []) || []).length) + "</dd>" +
            "<dt>" + esc(t("Start at login")) + "</dt><dd>" + esc(val("start_at_login", true) ? t("yes") : t("no")) + "</dd>" +
            "</dl>",
          foot: '<span class="small muted">' + esc(t("You can re-run this wizard any time from Settings \u203a About.")) + '</span>'
        });
      }
    }
  ];

  /* ── 9. Navigation + rendering ─────────────────────────────────────── */
  function renderNav() {
    var host = $("#navList");
    if (!host) return;
    var h = "";
    if (S.mode === "wizard") {
      WIZARD.forEach(function (w, i) {
        var cur = i === S.step;
        h += '<button type="button" class="nav-item' + (i < S.step ? " done" : "") + '"' +
          (cur ? ' aria-current="page"' : "") + ' data-act="wiz-goto" data-i="' + i + '">' +
          '<span class="nav-step">' + (i < S.step ? "\u2713" : i + 1) + "</span>" +
          '<span class="nav-txt"><b>' + esc(w.label) + "</b>" +
          (w.sub ? '<i>' + esc(w.sub) + "</i>" : "") + "</span></button>";
      });
    } else {
      SECTIONS.forEach(function (s) {
        var cur = s.id === S.section;
        h += '<button type="button" class="nav-item"' + (cur ? ' aria-current="page"' : "") +
          ' data-act="nav" data-id="' + esc(s.id) + '">' +
          '<span class="nav-ic">' + icon(s.ic) + '</span><span class="nav-txt">' + esc(s.label) + "</span></button>";
      });
      h += '<div class="nav-sep"></div>' +
        '<button type="button" class="nav-item" data-act="wizard-restart"><span class="nav-ic">' + icon("spark") +
        '</span><span class="nav-txt">' + esc(t("Run setup guide")) + '</span></button>';
    }
    host.innerHTML = h;
  }

  function wizardBody() {
    var step = WIZARD[S.step];
    var p = ((S.step) / (WIZARD.length - 1)) * 100;
    var head = '<div class="wiz-steps">' +
      '<span class="wiz-count">' + esc(t("Step {n} of {total}", { n: S.step + 1, total: WIZARD.length })) + "</span>" +
      '<span class="ws-bar"><i style="width:' + p.toFixed(1) + '%"></i></span>' +
      '<span>' + esc(step.label) + "</span></div>";
    var nav = '<div class="wiz-nav">' +
      '<button type="button" class="btn ghost" data-act="wiz-back"' + (S.step === 0 ? " disabled" : "") + ">" + esc(t("Back")) + "</button>" +
      '<span class="spacer"></span>' +
      '<button type="button" class="btn" data-act="smoke-test">' + icon("wave") + " " + esc(t("Run smoke test")) + "</button>" +
      (S.step === WIZARD.length - 1
        ? '<button type="button" class="btn primary" data-act="finish-setup">' + icon("check") + " " + esc(t("Finish setup")) + "</button>"
        : '<button type="button" class="btn primary" data-act="wiz-next">' + esc(t("Next")) + " " + icon("arrow") + "</button>") +
      "</div>";
    return '<div class="page wiz">' + head + wizHero(step) + step.body() + nav + "</div>";
  }

  /* The hero: eyebrow, the headline with the product name picked out, the
     paragraph, and the sphere. The sphere is a decorative sibling rather than
     a background image so it can never sit on top of the text. */
  function wizHero(step) {
    var title = esc(step.title).replace(/Jarvis/g, '<b>Jarvis</b>');
    return '<section class="hero">' +
      '<div class="hero-txt">' +
      '<p class="eyebrow">' + esc(step.eyebrow || t("Setup your Jarvis")) + "</p>" +
      "<h2>" + title + "</h2>" +
      '<p class="hero-para">' + esc(step.para || "") + "</p>" +
      "</div>" +
      '<div class="hero-orb" aria-hidden="true"><span></span>' +
      '<p class="hero-quote">\u201c' + esc(t("A more capable you.")) + "\u201d</p></div>" +
      "</section>";
  }

  function settingsBody() {
    var s = null;
    for (var i = 0; i < SECTIONS.length; i++) if (SECTIONS[i].id === S.section) s = SECTIONS[i];
    if (!s) s = SECTIONS[0];
    return '<div class="page">' + s.render() + "</div>";
  }

  var view = $("#view");
  function render(opts) {
    opts = opts || {};
    applyDir();
    applyTheme();
    applyStaticText();
    renderNav();
    if (!view) return;
    view.innerHTML = S.mode === "wizard" ? wizardBody() : settingsBody();
    var sub = $("#brandSub");
    if (sub) sub.textContent = t("Your AI. On your terms.");
    var sh = $("#sideHead");
    if (sh) sh.textContent = S.mode === "wizard" ? t("Setup") : t("Settings");
    setStatus(S.status, S.statusDetail, true);
    syncThemeButton();
    if (opts.scroll) view.scrollTop = 0;
    if (opts.focus) { try { view.focus({ preventScroll: true }); } catch (e) { view.focus(); } }
    if (opts.keepInput) {
      var el = $('[data-input="' + opts.keepInput + '"][data-key="' + (opts.keepKey || "") + '"]') ||
        $('[data-input="' + opts.keepInput + '"]');
      if (el) el.focus();
    }
  }

  function syncThemeButton() {
    var btn = $('[data-act="theme"]');
    if (!btn) return;
    var thm = val("theme", "dark");
    btn.innerHTML = thm === "light"
      ? '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.4A8.4 8.4 0 0 1 9.6 4 8.6 8.6 0 1 0 20 14.4z"/></svg>'
      : '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.6v2.2M12 19.2v2.2M4.2 12H2M22 12h-2.2M6.2 6.2 4.6 4.6M19.4 19.4l-1.6-1.6M17.8 6.2l1.6-1.6M4.6 19.4l1.6-1.6"/></svg>';
    btn.setAttribute("aria-label", thm === "light" ? t("Switch to dark theme") : t("Switch to light theme"));
  }

  /* ── 10. Status pill + sidebar status ──────────────────────────────── */
  var STATES = {
    sleeping: t("Sleeping"), dictating: t("Dictating"), listening: t("Listening"), thinking: t("Thinking"),
    working: t("Working"), approval: t("Approval needed"), error: t("Error"), muted: t("Muted")
  };
  // What the header pill SAYS, as opposed to what the state is called. The pill
  // is the app talking to you, so it uses a sentence; the sidebar keeps the
  // one-word state for when you need the precise thing.
  var SAYS = {
    sleeping: t("Ready when you are."), dictating: t("Listening — keep talking."),
    listening: t("Listening — keep talking."), thinking: t("Thinking it through…"),
    working: t("Working on it…"), approval: t("Waiting for your OK."),
    error: t("Something went wrong."), muted: t("Microphone paused.")
  };
  var SIDE_STATE = {
    sleeping: t("Jarvis is sleeping"), dictating: t("Jarvis is listening"),
    listening: t("Jarvis is listening"), thinking: t("Jarvis is thinking"),
    working: t("Jarvis is working"), approval: t("Jarvis needs your OK"),
    error: t("Jarvis hit an error"), muted: t("Microphone paused")
  };
  function setStatus(name, detail, silent) {
    var key = STATES[name] ? name : "sleeping";
    S.status = key;
    S.statusDetail = detail || "";
    var pill = $("#statusPill");
    if (pill) {
      pill.setAttribute("data-state", key);
      var lbl = $("#statusLabel");
      if (lbl) lbl.textContent = SAYS[key] || STATES[key];
    }
    var ss = $("#sideState"); if (ss) ss.textContent = SIDE_STATE[key] || STATES[key];
    var sd = $("#sideDetail");
    if (sd) sd.textContent = S.statusDetail ||
      (key === "sleeping" ? t("Say your wake word to start") : STATES[key]);
    var w = $("#sideWave");
    if (w) w.classList.toggle("active", key === "listening" || key === "dictating" || key === "thinking" || key === "working");
    if (!silent) announce(t("Status: {state}", { state: STATES[key] }) + (S.statusDetail ? ". " + S.statusDetail : ""));
  }

  /* ── 11. Theme ─────────────────────────────────────────────────────── */
  function toggleTheme() {
    var next = val("theme", "dark") === "light" ? "dark" : "light";
    store("theme", next);
    applyTheme();
    syncThemeButton();
    saveConfig({ theme: next }, true);
    announce(t("Theme set to {value}", { value: next === "light" ? t("Light") : t("Dark") }));
  }

  /* ── 12. Actions ───────────────────────────────────────────────────── */
  function setMode(mode) {
    S.mode = mode;
    S.connResult = null;
    render({ scroll: true, focus: true });
  }

  function goStep(i) {
    S.step = clamp(i, 0, WIZARD.length - 1);
    S.connResult = null;
    render({ scroll: true, focus: true });
  }

  function loadUsage(force) {
    if (S.loadedUsage && !force) return Promise.resolve();
    return call("get_usage").then(function (u) { if (u) { S.usage = u; S.loadedUsage = true; } });
  }
  function loadMemory(force) {
    if (S.loadedMemory && !force) return Promise.resolve();
    return call("get_memory").then(function (m) { if (m) { S.memory = m; S.loadedMemory = true; } });
  }
  function loadAgents(force) {
    if (S.loadedAgents && !force) return Promise.resolve();
    return call("get_agent_status").then(function (a) { if (a) { S.agents = a; S.loadedAgents = true; } });
  }

  function loadHistory(force) {
    if (S.loadedHistory && !force) return Promise.resolve();
    return call("get_history").then(function (h) { if (h) { S.history = h; S.loadedHistory = true; } });
  }

  function loadVoices(force) {
    if (S.loadedVoices && !force) return Promise.resolve();
    return Promise.all([
      call("get_voices").then(function (v) { if (v) S.voices = v; }),
      call("get_command_help").then(function (c) { if (c) S.commandHelp = c; })
    ]).then(function () { S.loadedVoices = true; });
  }
  function loadDevices() {
    return call("get_devices").then(function (d) { if (d) S.devices = d; });
  }

  function showRecorder(which) {
    var b = null;
    for (var i = 0; i < BINDINGS.length; i++) if (BINDINGS[i].k === which) b = BINDINGS[i];
    var m = $("#recorder");
    if (!m) return;
    var tt = $("#recTitle"); if (tt) tt.textContent = t("Listening \u2014 {name}", { name: b ? b.t : which });
    var d = $("#recDesc");
    if (d) d.textContent = t("Press the key combination or mouse button for \u201c{name}\u201d. Press Escape in this dialog to stop waiting here.", { name: b ? b.t : which });
    m.hidden = false;
    S.recorderOpen = true;
    var c = $(".modal-card button", m); if (c) c.focus();
  }
  function hideRecorder() {
    var m = $("#recorder");
    if (m) m.hidden = true;
    S.recorderOpen = false;
  }

  function recordBinding(which) {
    showRecorder(which);
    call("record_binding", which).then(function (res) {
      hideRecorder();
      if (!res) return;
      if (!res.ok) { toast("warn", t("Shortcut capture was cancelled.")); return; }
      // Save the CANONICAL binding, never the display label. `record_binding`
      // returns both: "xbutton1" and "Mouse button 4". Saving the label left the
      // hotkey layer holding a value the mouse hook can never produce, so the
      // mouse side button simply did nothing - with no error anywhere.
      var canonical = res.binding || res.display;
      var display = res.display || canonical;
      var patch = bindingPatch(which, canonical);
      var c = cfg();
      if (patch.shortcuts) { c.shortcuts = patch.shortcuts; } else { c[which] = canonical; }
      if (res.conflict) {
        if (patch.shortcuts) patch.shortcuts[which] = { display: display, conflict: res.conflict };
        else patch[which] = { display: display, conflict: res.conflict };
        toast("warn", t("That shortcut is already used by another app: {name}", { name: res.conflict }));
      } else {
        toast("info", t("Shortcut set to {value}.", { value: display }));
      }
      return saveConfig(patch, true).then(function () { render(); });
    });
  }

  function runConnTest(mode) {
    if (S.connBusy) return;
    S.connBusy = mode;
    render();
    call("test_connection", mode).then(function (res) {
      S.connBusy = null;
      S.connResult = res || { ok: false, ms: 0, detail: t("No response from the app."), billed: false, cost_usd: 0, transcript: null };
      render();
      if (S.connResult.ok) toast("info", t("Connection test passed in {ms} ms.", { ms: S.connResult.ms }));
      else toast("error", t("Connection test failed."));
    });
  }

  function pickFolder() {
    call("pick_folder").then(function (res) {
      if (!res || !res.path) return;
      var list = (val("approved_paths", []) || []).slice();
      if (list.indexOf(res.path) === -1) list.push(res.path);
      store("approved_paths", list);
      return saveConfig({ approved_paths: list }).then(function () { render(); });
    });
  }
  function pickApp() {
    call("pick_app").then(function (res) {
      if (!res) return;
      var list = (val("approved_apps", []) || []).slice();
      var exists = list.some(function (x) { return typeof x === "object" ? x.path === res.path : x === res.path; });
      if (!exists) list.push({ name: res.name || res.path, path: res.path });
      store("approved_apps", list);
      return saveConfig({ approved_apps: list }).then(function () { render(); });
    });
  }

  function addToChipList(key, raw) {
    var text = String(raw || "").trim();
    if (!text) return;
    var list = (val(key, []) || []).slice();
    if (list.indexOf(text) !== -1) { toast("warn", t("That entry is already in the list.")); return; }
    list.push(text);
    store(key, list);
    saveConfig(prefixPatch(key, list)).then(function () {
      render({ keepInput: "chip-add", keepKey: key });
    });
  }
  function prefixPatch(key, list) {
    var p = {}; p[key] = list; return p;
  }
  function removeFromList(key, i) {
    var list = (val(key, []) || []).slice();
    list.splice(i, 1);
    store(key, list);
    saveConfig(prefixPatch(key, list)).then(function () { render(); });
  }

  function clearKey() {
    if (!window.confirm(t("Remove the stored API key? Dictation will stop working until you add another."))) return;
    call("clear_api_key").then(function () {
      if (S.state) S.state.key_fingerprint = null;
      toast("info", t("API key removed."));
      render();
    });
  }

  function doAction(act, el) {
    var key, i, which;
    switch (act) {
      case "nav":
        S.section = el.getAttribute("data-id");
        S.connResult = null;
        if (S.section === "budgets") loadUsage(false).then(function () { render(); });
        if (S.section === "memory") loadMemory(false).then(function () { render(); });
        if (S.section === "history") loadHistory(false).then(function () { render(); });
        if (S.section === "assistant" || S.section === "dictation") loadVoices(false).then(function () { render(); });
        if (S.section === "integrations") loadAgents(false).then(function () { render(); });
        render({ scroll: true, focus: true });
        break;

      case "wiz-goto": goStep(parseInt(el.getAttribute("data-i"), 10)); break;
      case "wiz-next": goStep(S.step + 1); break;
      case "wiz-back": goStep(S.step - 1); break;
      case "wiz-skip": goStep(S.step + 1); break;
      case "wizard-restart":
        S.step = 0; S.mode = "wizard"; S.connResult = null;
        render({ scroll: true, focus: true });
        break;

      case "finish-setup":
        saveConfig({ setup_complete: true }, true).then(function () {
          S.state.setup_complete = true;
          S.mode = "settings";
          S.section = "general";
          render({ scroll: true, focus: true });
          toast("info", t("Setup complete. Jarvis is ready."));
        });
        break;

      case "theme": toggleTheme(); break;
      case "quit":
        if (window.confirm(t("Quit Jarvis? The tray icon and wake listener will stop."))) call("quit_app");
        break;

      case "toggle": {
        var sw = el;
        var on = sw.getAttribute("aria-checked") !== "true";
        sw.setAttribute("aria-checked", on ? "true" : "false");
        key = sw.getAttribute("data-key");
        store(key, on);
        if (key === "reduced_motion") applyTheme();
        if (key === "start_at_login") call("set_start_at_login", on);
        saveConfig(prefixPatch(key, on));
        break;
      }

      case "seg": {
        key = el.getAttribute("data-key");
        var v = el.getAttribute("data-val");
        store(key, v);
        saveConfig(prefixPatch(key, v), true);
        render();
        break;
      }

      case "key-reveal": {
        var inp = $("#apikey");
        if (!inp) break;
        var show = inp.type === "password";
        inp.type = show ? "text" : "password";
        el.setAttribute("aria-pressed", show ? "true" : "false");
        el.innerHTML = show ? icon("eyeoff") : icon("eye");
        inp.focus();
        break;
      }

      case "key-save": {
        var ki = $("#apikey");
        var raw = ki ? ki.value.trim() : "";
        if (!raw) { S.keyErr = t("Enter a key before saving."); render(); break; }
        S.keyErr = "";
        call("set_api_key", raw).then(function (res) {
          if (!res || !res.ok) {
            S.keyErr = (res && res.error) || t("That key was rejected by the app.");
            render();
            toast("error", S.keyErr);
            return;
          }
          if (S.state) S.state.key_fingerprint = res.fingerprint || null;
          toast("info", t("API key saved."));
          render();
        });
        break;
      }

      case "clear-key": clearKey(); break;
      case "conn-test": runConnTest(el.getAttribute("data-mode")); break;

      case "mic-test":
        if (S.micTesting) { S.micTesting = false; render(); break; }
        S.micTesting = true;
        render();
        call("list_mics_test").then(function () { toast("info", t("Microphone level test started \u2014 say something.")); });
        break;

      case "record": recordBinding(el.getAttribute("data-which")); break;
      case "rec-cancel": hideRecorder(); break;

      case "wake-download": {
        if (S.wakeBusy) break;
        S.wakeBusy = true;
        render();
        toast("info", t("Downloading the wake-word model \u2014 about 20 MB, once."));
        call("download_wake_model").then(function (res) {
          S.wakeBusy = false;
          // Force a fresh status read: the model may or may not have landed.
          S.wakeModel = undefined;
          S.wakeFetching = false;
          if (res && res.ok) {
            toast("info", t("Wake-word model installed. The listener starts automatically."));
          } else {
            toast("error", t("Could not download the model: {why}",
                             { why: (res && res.detail) || t("unknown error") }));
          }
          render();
        });
        break;
      }

      case "pick-folder": pickFolder(); break;
      case "pick-app": pickApp(); break;

      case "chip-del": {
        key = el.getAttribute("data-key");
        i = parseInt(el.getAttribute("data-i"), 10);
        removeFromList(key, i);
        break;
      }
      case "list-del": {
        key = el.getAttribute("data-key");
        i = parseInt(el.getAttribute("data-i"), 10);
        removeFromList(key, i);
        break;
      }

      case "wake-setup":
        el.disabled = true;
        call("run_wake_setup").then(function (res) {
          el.disabled = false;
          if (res && res.ok) toast("info", res.detail || t("Wake-word setup finished."));
          else toast("error", (res && res.detail) || t("Wake-word setup failed."));
        });
        break;

      case "calibrate":
        call("start_calibration").then(function () {
          toast("info", t("Accuracy comparison started. Keep speaking \u2014 this is billable."));
        });
        break;

      case "test-insert":
        call("test_insert_text").then(function (res) {
          var out = $("#insertResult");
          var msg = res && res.ok ? (res.detail || t("Sample text inserted.")) : (res && res.detail) || t("Insert failed.");
          if (out) out.textContent = msg;
          toast(res && res.ok ? "info" : "error", msg);
        });
        break;

      case "forget":
        call("forget_memory", num(el.getAttribute("data-id"), 0)).then(function () {
          S.loadedMemory = false;
          loadMemory(true).then(function () { render(); });
        });
        break;

      case "clear-history":
        if (!window.confirm(t("Clear all stored transcripts? This cannot be undone."))) break;
        call("clear_history").then(function () { toast("info", t("Transcript history cleared.")); });
        break;

      case "usage-refresh":
        loadUsage(true).then(function () { render(); toast("info", t("Usage refreshed.")); });
        break;
      case "memory-refresh":
        loadMemory(true).then(function () { render(); toast("info", t("Memory refreshed.")); });
        break;

      case "open-settings":
        S.mode = "settings";
        S.section = "general";
        render({ nav: true });
        break;
      case "smoke-test":
        // The free, offline end-to-end check. Runs in the tray process and
        // reports back through the normal toast channel.
        toast("info", t("Running the smoke test…"));
        call("run_smoke_test").then(function (r) {
          if (!r) return;
          toast(r.ok ? "info" : "warn", r.summary || (r.ok ? t("Smoke test passed.") : t("Smoke test failed.")));
        });
        break;

      case "history-refresh":
        loadHistory(true).then(function () { render(); toast("info", t("History refreshed.")); });
        break;
      case "history-copy": {
        var hid = num(el.getAttribute("data-id"), 0);
        var items = (S.history && S.history.items) || [];
        var hit = null, hi;
        for (hi = 0; hi < items.length; hi++) if (items[hi].id === hid) hit = items[hi];
        if (!hit) { toast("warn", t("That entry is no longer in the list.")); break; }
        call("copy_text", hit.text).then(function (r) {
          if (r && r.ok) toast("info", t("Copied {n} characters to the clipboard.", { n: r.chars }));
          else toast("warn", (r && r.error) || t("Could not copy that."));
        });
        break;
      }
      case "pick-voice":
        (store("voice", el.getAttribute("data-val")), saveConfig({ voice: el.getAttribute("data-val") })).then(function () {
          loadVoices(true).then(function () { render(); });
          toast("info", t("Voice set. It applies to the next assistant session."));
        });
        break;
      case "agents-refresh":
        loadAgents(true).then(function () { render(); toast("info", t("Agent status refreshed.")); });
        break;
      default: break;
    }
  }

  /* ── 13. Event wiring (delegated, no inline handlers) ──────────────── */
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-act]");
    if (!el) return;
    var act = el.getAttribute("data-act");
    if (el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "OPTION") return;
    doAction(act, el);
  });

  // The history filter is client-side over rows already loaded, so it can run
  // on every keystroke without a round trip.
  document.addEventListener("input", function (e) {
    var el = e.target;
    if (!el || el.getAttribute("data-act") !== "history-query") return;
    S.historyQuery = el.value || "";
    var host = document.querySelector(".card-body");
    render();
    var again = document.getElementById("hist_q");
    if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); }
  });

  document.addEventListener("change", function (e) {
    var el = e.target;
    if (!el || el.getAttribute("data-act") !== "voice-select") return;
    (store("voice", el.value), saveConfig({ voice: el.value })).then(function () {
      loadVoices(true).then(function () { render(); });
      toast("info", t("Voice set. It applies to the next assistant session."));
    });
  });

  document.addEventListener("submit", function (e) {
    var f = e.target.closest("[data-form='chip-add']");
    if (!f) return;
    e.preventDefault();
    var key = f.getAttribute("data-key");
    var inp = $("[data-input='chip-add'][data-key='" + key + "']", f);
    if (inp) { addToChipList(key, inp.value); inp.value = ""; }
  });

  document.addEventListener("input", function (e) {
    var el = e.target;
    if (!el.getAttribute) return;

    if (el.getAttribute("data-act") === "range") {
      var key = el.getAttribute("data-key");
      var v = num(el.value, 0);
      var min = num(el.getAttribute("min"), 0), max = num(el.getAttribute("max"), 1);
      el.style.setProperty("--pct", (((v - min) / (max - min)) * 100).toFixed(1) + "%");
      var out = $("#sv_" + key);
      if (out) out.textContent = v.toFixed(2);
      store(key, v);
      debounce("range:" + key, function () { saveConfig(prefixPatch(key, v), true); }, 400);
      return;
    }

    if (el.getAttribute("data-act") === "number") {
      var nk = el.getAttribute("data-key");
      debounce("num:" + nk, function () {
        var nv = Number(el.value);
        if (!isFinite(nv)) return;
        if (nk === "daily_budget_usd" || nk === "monthly_budget_usd") nv = Math.max(0, nv);
        store(nk, nv);
        saveConfig(prefixPatch(nk, nv));
        if (el.isConnected && document.activeElement !== el) el.value = nv;
      }, 400);
      return;
    }

    if (el.getAttribute("data-act") === "text") {
      var tk = el.getAttribute("data-key");
      var tv = el.value;
      debounce("txt:" + tk, function () {
        if (tk === "vocab") return;
        store(tk, tv);
        saveConfig(prefixPatch(tk, tv));
      }, 400);
    }
  });

  document.addEventListener("change", function (e) {
    var el = e.target;
    if (!el.getAttribute) return;
    if (el.getAttribute("data-act") === "select") {
      var key = el.getAttribute("data-key");
      var value = el.value;
      store(key, value);
      if (key === "ui_language") { setLang(value); return; }
      saveConfig(prefixPatch(key, value));
    }
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && S.recorderOpen) { hideRecorder(); announce(t("Shortcut capture cancelled.")); }
    if (e.key === "Enter" && S.recorderOpen) { /* capture is owned by the app; do not swallow */ }
  });

  /* ── 14. Backend → page event bus ──────────────────────────────────── */
  function handleBus(name, payload) {
    payload = payload || {};
    switch (name) {
      case "mic_level": {
        S.micLevel = clamp(num(payload.rms, 0), 0, 1);
        var bar = $("#micMeter > i");
        if (bar) bar.style.width = (S.micLevel * 100).toFixed(1) + "%";
        var v = $("#micVal");
        if (v) v.textContent = t("level") + " " + S.micLevel.toFixed(3);
        break;
      }
      case "status": {
        setStatus(payload.state, payload.detail);
        break;
      }
      case "calibration_progress": {
        S.calib = payload;
        var host = view && view.querySelector('[data-act="calibrate"]');
        var blockHost = view && view.querySelector(".progress");
        if (blockHost || host) {
          var p = (num(payload.index, 0) / Math.max(1, num(payload.total, 1))) * 100;
          if (blockHost) blockHost.querySelector("i").style.width = p.toFixed(1) + "%";
          var line = view.querySelector(".phase-line");
          if (line) {
            line.textContent = t("Sample {n} of {total} \u00b7 {phase}", { n: num(payload.index, 0), total: num(payload.total, 1), phase: payload.phase || t("working") }) +
              (payload.text ? ' \u00b7 \u201c' + payload.text + "\u201d" : "");
          }
        } else {
          render();
        }
        break;
      }
      case "toast": {
        toast(payload.level || "info", payload.message || "");
        break;
      }
      default:
        return;
    }
  }
  window.hermesBus = window.bvBus = handleBus;

  /* ── 15. Boot ──────────────────────────────────────────────────────── */
  function bootMsg(text) {
    var b = $("#bootSub");
    if (b) b.textContent = text;
  }
  function failBoot(text) {
    var b = $("#bootSub");
    if (!b) return;
    b.textContent = text;
    b.insertAdjacentHTML("afterend",
      '<button type="button" class="btn mt12" id="bootRetry">' + esc(t("Retry")) + '</button>');
    var r = $("#bootRetry");
    if (r) r.addEventListener("click", function () { window.location.reload(); });
  }

  function start() {
    applyDir();
    applyStaticText();
    waitForApi(15000).then(function (ok) {
      if (!ok) {
        failBoot("Jarvis backend is not responding. Start the app, then reload this window.");
        return;
      }
      bootMsg(t("Loading your settings\u2026"));
      return call("get_state").then(function (st) {
        if (!st) { failBoot("The app did not return a settings state. Try reloading."); return; }
        S.state = st;
        if (st.config && st.config.ui_language) {
          var persisted = normalizeLang(st.config.ui_language);
          if (persisted && persisted !== currentLang) {
            // Strings built once at load time (the wizard step labels, the nav
            // list, the status maps) were evaluated in the language detected at
            // load. If the saved setting differs, mirror it and reload exactly
            // once so those constants come back in the chosen language. The
            // localStorage mirror makes detectLang() agree on the next load, so
            // this cannot loop; if storage is unavailable we degrade gracefully
            // (render-time strings still translate) rather than loop.
            var mirrored = false;
            try {
              window.localStorage.setItem("jarvis_ui_language", persisted);
              mirrored = true;
            } catch (e) { mirrored = false; }
            if (mirrored) { window.location.reload(); return; }
            currentLang = persisted; applyDir(); applyStaticText();
          }
        }
        if (st.config && st.config.theme) { /* theme from config */ }
        if (st.vocab && st.config && !st.config.vocab) st.config.vocab = st.vocab.slice();
        applyTheme();
        var boot = $("#boot");
        var app = $("#app");
        if (app) app.hidden = false;
        if (boot) { boot.classList.add("gone"); setTimeout(function () { boot.hidden = true; }, 320); }
        var sv = $("#sideVersion"); if (sv) sv.textContent = "v" + (st.version || "\u2014");
        S.mode = st.setup_complete ? "settings" : "wizard";
        S.section = "general";
        S.step = 0;
        render({ scroll: true });
        loadDevices().then(function () {
          if (S.mode === "settings" && (S.section === "general" || S.section === "dictation")) render();
          if (S.mode === "wizard" && WIZARD[S.step].id === "audio") render();
        });
        setStatus("sleeping", st.config && st.config.wake_enabled
          ? t("Wake word enabled; tray must be running")
          : t("Wake word off; use your dictation shortcut"));
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else start();
})();
