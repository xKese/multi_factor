/* M&S Theme-Toggle
 * Setzt data-theme auf <html>, persistiert in localStorage.
 * Läuft vor Dash-Render, damit kein Flicker entsteht.
 */
(function () {
  try {
    var stored = localStorage.getItem("ms-theme");
    if (stored === "dark" || stored === "light") {
      document.documentElement.setAttribute("data-theme", stored);
    } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
      document.documentElement.setAttribute("data-theme", "dark");
    } else {
      document.documentElement.setAttribute("data-theme", "light");
    }
  } catch (e) {
    document.documentElement.setAttribute("data-theme", "light");
  }

  function syncThemeButton() {
    var btn = document.getElementById("ms-theme-btn");
    if (!btn) return false;
    var cur = document.documentElement.getAttribute("data-theme") || "light";
    btn.setAttribute("aria-pressed", cur === "dark" ? "true" : "false");
    btn.setAttribute(
      "aria-label",
      cur === "dark" ? "Zu hellem Theme wechseln" : "Zu dunklem Theme wechseln"
    );
    return true;
  }
  window.msSyncThemeButton = syncThemeButton;

  window.msToggleTheme = function () {
    var cur = document.documentElement.getAttribute("data-theme") || "light";
    var next = cur === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("ms-theme", next); } catch (e) {}
    syncThemeButton();
    return next;
  };

  function schedulInitialSync() {
    if (syncThemeButton()) return;
    var obs = new MutationObserver(function () {
      if (syncThemeButton()) obs.disconnect();
    });
    obs.observe(document.body || document.documentElement, {
      childList: true,
      subtree: true,
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", schedulInitialSync);
  } else {
    schedulInitialSync();
  }
})();

// Dash clientside-Namespace für den Theme-Toggle.
window.dash_clientside = window.dash_clientside || {};
window.dash_clientside.ms = {
  toggleTheme: function (n_clicks, current) {
    var next = window.msToggleTheme();
    return next;
  },
  applyStoredTheme: function () {
    try {
      var stored = localStorage.getItem("ms-theme");
      if (stored === "dark" || stored === "light") {
        document.documentElement.setAttribute("data-theme", stored);
        return stored;
      }
    } catch (e) {}
    return document.documentElement.getAttribute("data-theme") || "light";
  },
  cmdkSyncData: function (data) {
    window.msCmdk = window.msCmdk || {};
    window.msCmdk.items = Array.isArray(data) ? data : [];
    return window.dash_clientside.no_update;
  }
};

/* ==========================================================================
   Command Palette — Ticker-Schnellsuche (Cmd+K / Ctrl+K / "/")
   Overlay-Markup und Daten-Store liefert Dash; Filter + Navigation laufen
   vollständig clientseitig gegen ``window.msCmdk.items``.
   ========================================================================== */
(function () {
  var MAX_RESULTS = 8;

  function getOverlay() { return document.getElementById("ms-cmdk"); }
  function getInput()   { return document.getElementById("ms-cmdk-input"); }
  function getResults() { return document.getElementById("ms-cmdk-results"); }

  function isOpen() {
    var el = getOverlay();
    return !!(el && el.classList.contains("is-open"));
  }

  function open() {
    var el = getOverlay();
    var input = getInput();
    if (!el || !input) return;
    el.classList.add("is-open");
    el.setAttribute("aria-hidden", "false");
    input.value = "";
    render(filterItems(""));
    setTimeout(function () { input.focus(); }, 0);
  }

  function close() {
    var el = getOverlay();
    if (!el) return;
    el.classList.remove("is-open");
    el.setAttribute("aria-hidden", "true");
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (m) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m];
    });
  }

  function filterItems(query) {
    var items = (window.msCmdk && window.msCmdk.items) || [];
    if (!query) return items.slice(0, MAX_RESULTS);
    var q = query.toLowerCase();
    var scored = [];
    for (var i = 0; i < items.length; i++) {
      var it = items[i];
      var t = (it.ticker || "").toLowerCase();
      var n = (it.name || "").toLowerCase();
      var s = -1;
      if (t === q) s = 100;
      else if (t.indexOf(q) === 0) s = 90;
      else if (t.indexOf(q) >= 0) s = 80;
      else if (n.indexOf(q) === 0) s = 70;
      else if (n.indexOf(q) >= 0) s = 60;
      if (s >= 0) scored.push({ it: it, s: s, t: t });
    }
    scored.sort(function (a, b) {
      if (b.s !== a.s) return b.s - a.s;
      return a.t < b.t ? -1 : a.t > b.t ? 1 : 0;
    });
    var out = [];
    for (var k = 0; k < scored.length && k < MAX_RESULTS; k++) out.push(scored[k].it);
    return out;
  }

  function render(results) {
    var box = getResults();
    if (!box) return;
    if (!results.length) {
      box.innerHTML = '<div class="ms-cmdk-empty">Keine Treffer.</div>';
      return;
    }
    var html = "";
    for (var i = 0; i < results.length; i++) {
      var r = results[i];
      html +=
        '<div class="ms-cmdk-item' + (i === 0 ? " is-active" : "") +
        '" role="option" data-ticker="' + escapeHtml(r.ticker) + '">' +
        '<span class="ms-cmdk-ticker">' + escapeHtml(r.ticker) + "</span>" +
        '<span class="ms-cmdk-name">' + escapeHtml(r.name || "") + "</span>" +
        (r.sector ? '<span class="ms-cmdk-sector">' + escapeHtml(r.sector) + "</span>" : "") +
        "</div>";
    }
    box.innerHTML = html;
  }

  function move(delta) {
    var box = getResults();
    if (!box) return;
    var items = box.querySelectorAll(".ms-cmdk-item");
    if (!items.length) return;
    var idx = -1;
    for (var i = 0; i < items.length; i++) {
      if (items[i].classList.contains("is-active")) { idx = i; break; }
    }
    idx = (idx + delta + items.length) % items.length;
    for (var j = 0; j < items.length; j++) {
      items[j].classList.toggle("is-active", j === idx);
    }
    items[idx].scrollIntoView({ block: "nearest" });
  }

  function activate() {
    var box = getResults();
    if (!box) return;
    var active = box.querySelector(".ms-cmdk-item.is-active") ||
                 box.querySelector(".ms-cmdk-item");
    if (!active) return;
    navigate(active.getAttribute("data-ticker"));
  }

  function navigate(ticker) {
    if (!ticker) return;
    var url = "/einzelanalyse?ticker=" + encodeURIComponent(ticker);
    try {
      window.history.pushState({}, "", url);
      window.dispatchEvent(new PopStateEvent("popstate", { state: {} }));
    } catch (e) {
      window.location.href = url;
    }
    close();
  }

  document.addEventListener("keydown", function (e) {
    var key = e.key;
    var mod = e.metaKey || e.ctrlKey;
    var target = e.target || {};
    var tag = (target.tagName || "").toUpperCase();
    var inField = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" ||
                  target.isContentEditable;
    var inCmdk = target.id === "ms-cmdk-input";

    if (mod && (key === "k" || key === "K")) {
      e.preventDefault();
      isOpen() ? close() : open();
      return;
    }
    if (key === "/" && !inField && !isOpen()) {
      e.preventDefault();
      open();
      return;
    }
    if (!isOpen()) return;
    if (key === "Escape") { e.preventDefault(); close(); return; }
    if (!inCmdk) return;
    if (key === "ArrowDown") { e.preventDefault(); move(1); return; }
    if (key === "ArrowUp")   { e.preventDefault(); move(-1); return; }
    if (key === "Enter")     { e.preventDefault(); activate(); return; }
  });

  document.addEventListener("input", function (e) {
    if (e.target && e.target.id === "ms-cmdk-input") {
      render(filterItems(e.target.value));
    }
  });

  document.addEventListener("click", function (e) {
    var t = e.target;
    if (!t) return;
    if (t.classList && t.classList.contains("ms-cmdk-backdrop")) {
      close();
      return;
    }
    var item = t.closest ? t.closest(".ms-cmdk-item") : null;
    if (item && isOpen()) {
      navigate(item.getAttribute("data-ticker"));
    }
  });

  document.addEventListener("mouseover", function (e) {
    var t = e.target;
    var item = t && t.closest ? t.closest(".ms-cmdk-item") : null;
    if (!item || !isOpen()) return;
    var box = getResults();
    if (!box) return;
    var items = box.querySelectorAll(".ms-cmdk-item");
    for (var i = 0; i < items.length; i++) {
      items[i].classList.toggle("is-active", items[i] === item);
    }
  });
})();
