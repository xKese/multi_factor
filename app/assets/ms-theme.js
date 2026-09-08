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
        '" role="option" data-ticker="' + escapeHtml(r.uid || r.ticker) + '">' +
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

/* ==========================================================================
   Priority+-Navigation — „Mehr ▾"-Überlauf und Zahnrad-Menü
   Dash rendert nur die zehn Tabs und zwei leere Panels; welche Tabs in die
   Breite passen, entscheidet sich erst hier. Die Einträge des Überlauf-Menüs
   werden bei jeder Neuberechnung aus den gerade versteckten Tabs erzeugt.
   ========================================================================== */
(function () {
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (m) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m];
    });
  }

  /* Seitenwechsel ohne Reload — derselbe Weg, den auch dcc.Link und die
     Command-Palette gehen: pushState plus künstliches popstate-Event, auf
     das dcc.Location hört. */
  function go(href) {
    if (!href) return;
    try {
      window.history.pushState({}, "", href);
      window.dispatchEvent(new PopStateEvent("popstate", { state: {} }));
    } catch (e) {
      window.location.href = href;
    }
  }

  /* ---------------------------------------------------------------------
     Generische Menü-Steuerung (Menu-Button-Muster, WAI-ARIA)
     Zweimal instanziiert: für „Mehr ▾" und für das Zahnrad.
     --------------------------------------------------------------------- */
  var MENUS = [];
  function closeOthers(except) {
    for (var i = 0; i < MENUS.length; i++) {
      if (MENUS[i] !== except) MENUS[i].close(false);
    }
  }

  function createMenu(boxId, btnId, panelId, itemSelector) {
    var api = {};

    function box() { return document.getElementById(boxId); }
    function btn() { return document.getElementById(btnId); }
    function panel() { return document.getElementById(panelId); }

    function items() {
      var p = panel();
      return p ? p.querySelectorAll(itemSelector) : [];
    }

    function isOpen() {
      var b = box();
      return !!(b && b.classList.contains("is-open"));
    }

    function focusItem(idx) {
      var list = items();
      if (!list.length) return;
      idx = (idx + list.length) % list.length;
      list[idx].focus();
    }

    function open(focusLast) {
      var b = box(), t = btn();
      if (!b || !t) return;
      closeOthers(api);
      b.classList.add("is-open");
      t.setAttribute("aria-expanded", "true");
      focusItem(focusLast ? -1 : 0);
    }

    function close(restoreFocus) {
      var b = box(), t = btn();
      if (!b || !b.classList.contains("is-open")) return;
      b.classList.remove("is-open");
      if (t) {
        t.setAttribute("aria-expanded", "false");
        if (restoreFocus) t.focus();
      }
    }

    function currentIndex() {
      var list = items(), i;
      for (i = 0; i < list.length; i++) {
        if (list[i] === document.activeElement) return i;
      }
      return -1;
    }

    document.addEventListener("click", function (e) {
      var t = e.target;
      if (!t || !t.closest) return;
      if (t.closest("#" + btnId)) {
        e.preventDefault();
        isOpen() ? close(false) : open(false);
        return;
      }
      var item = t.closest(itemSelector);
      if (item && panel() && panel().contains(item)) {
        // Menüeinträge sind einfache <a> — Navigation selbst übernehmen,
        // damit kein voller Seiten-Reload entsteht.
        e.preventDefault();
        close(false);
        go(item.getAttribute("href"));
        return;
      }
      if (isOpen() && !t.closest("#" + boxId)) close(false);
    });

    document.addEventListener("keydown", function (e) {
      var t = e.target;
      var onBtn = t && t.closest && t.closest("#" + btnId);
      if (onBtn && !isOpen()) {
        if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open(false);
        } else if (e.key === "ArrowUp") {
          e.preventDefault();
          open(true);
        }
        return;
      }
      if (!isOpen()) return;
      if (e.key === "Escape") { e.preventDefault(); close(true); return; }
      if (e.key === "Tab") { close(false); return; }
      if (!t || !t.closest || !t.closest("#" + boxId)) return;
      if (e.key === "ArrowDown") { e.preventDefault(); focusItem(currentIndex() + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); focusItem(currentIndex() - 1); }
      else if (e.key === "Home") { e.preventDefault(); focusItem(0); }
      else if (e.key === "End") { e.preventDefault(); focusItem(-1); }
    });

    // Zurück-/Vorwärts-Navigation darf kein offenes Menü hinterlassen.
    window.addEventListener("popstate", function () { close(false); });

    api.close = close;
    api.isOpen = isOpen;
    MENUS.push(api);
    return api;
  }

  /* ---------------------------------------------------------------------
     Überlauf-Berechnung
     --------------------------------------------------------------------- */
  var W = [];          // natürliche (fette) Breiten der Tabs
  var MORE_W = 0;      // Breite des „Mehr"-Buttons
  var MEASURED = false;
  var LAST_SIG = "";
  var RAF = 0;
  var moreMenu = null;

  function nav() { return document.getElementById("ms-nav"); }
  function moreBox() { return document.getElementById("ms-nav-more"); }
  function moreBtn() { return document.getElementById("ms-nav-more-btn"); }
  function morePanel() { return document.getElementById("ms-nav-more-panel"); }
  function navLinks() {
    var n = nav();
    return n ? n.querySelectorAll(".ms-nav-link") : [];
  }

  /* Misst die Breiten einmalig — mit erzwungenem font-weight 600, weil
     .ms-nav-link.active fett ist und sonst je nach aktiver Seite andere
     Werte herauskämen. Alle Breiten sind damit Worst-Case-Werte.
     Liefert false, solange das Layout noch nicht steht (Breite 0). */
  function measure() {
    var n = nav(), box = moreBox(), label = moreBtn() && moreBtn().querySelector(".ms-nav-more-label");
    if (!n || !box) return false;
    var prevLabel = label ? label.textContent : null;
    if (label) label.textContent = "Mehr";
    n.classList.add("is-measuring");
    var ls = navLinks(), widths = [], ok = ls.length > 0, i, w;
    for (i = 0; i < ls.length; i++) {
      w = ls[i].getBoundingClientRect().width;
      if (!w) { ok = false; break; }
      widths.push(Math.ceil(w));
    }
    var mw = Math.ceil(box.getBoundingClientRect().width);
    n.classList.remove("is-measuring");
    if (label && prevLabel !== null) label.textContent = prevLabel;
    if (!ok || !mw) return false;
    W = widths;
    MORE_W = mw;
    MEASURED = true;
    return true;
  }

  /* Index des aktiven Tabs. Bevorzugt die Klasse, die der Dash-Callback
     setzt; der Pathname-Fallback greift beim ersten Rendern, bevor der
     Callback gelaufen ist (Deep-Link-Reload). */
  function activeIndex() {
    var ls = navLinks(), i, href;
    for (i = 0; i < ls.length; i++) {
      if (ls[i].classList.contains("active")) return i;
    }
    var p = window.location.pathname || "/";
    for (i = 0; i < ls.length; i++) {
      href = ls[i].getAttribute("href") || "";
      if (href === "/" ? p === "/" : (p === href || p.indexOf(href) === 0)) return i;
    }
    return -1;
  }

  /* Liefert {compact, keep}. Platz für „Mehr" wird erst reserviert, wenn
     feststeht, dass überhaupt etwas überläuft — sonst würde der Button
     unnötig einen Tab verdrängen. */
  function plan(avail, act) {
    var n = W.length, total = 0, i, keep = [], used = 0;
    for (i = 0; i < n; i++) total += W[i];
    if (total <= avail) {
      for (i = 0; i < n; i++) keep.push(i);
      return { compact: false, keep: keep };
    }

    var budget = avail - MORE_W;
    // Nicht einmal ein einziger Tab passt neben „Mehr": Kompaktmodus.
    if (act < 0 || budget < W[act]) return { compact: true, keep: [] };

    for (i = 0; i < n; i++) {
      if (used + W[i] > budget) break;
      used += W[i];
      keep.push(i);
    }
    // Der aktive Tab bleibt sichtbar — notfalls weichen die hinteren.
    if (keep.indexOf(act) < 0) {
      while (keep.length && used + W[act] > budget) used -= W[keep.pop()];
      keep.push(act);
    }
    return { compact: false, keep: keep };
  }

  function renderPanel(overflow, activeLink, compact) {
    var panel = morePanel(), box = moreBox(), btn = moreBtn();
    if (!panel || !box || !btn) return;
    var html = "", hasActive = false, i, a, on;
    for (i = 0; i < overflow.length; i++) {
      a = overflow[i];
      on = a.classList.contains("active");
      if (on) hasActive = true;
      html += '<a class="ms-nav-more-item' + (on ? " is-active" : "") +
              '" role="menuitem" tabindex="-1"' +
              (on ? ' aria-current="page"' : "") +
              ' href="' + escapeHtml(a.getAttribute("href") || "#") + '">' +
              escapeHtml(a.textContent || "") + "</a>";
    }
    panel.innerHTML = html;
    box.classList.toggle("has-active", hasActive);
    var label = btn.querySelector(".ms-nav-more-label");
    if (label) {
      label.textContent = (compact && activeLink) ? activeLink.textContent : "Mehr";
    }
    btn.setAttribute("title", compact ? "Navigation" : "Weitere Seiten");
  }

  function syncUtilMarker() {
    var util = document.getElementById("ms-util");
    if (!util) return;
    util.classList.toggle(
      "has-active",
      !!document.querySelector("#ms-util-panel .ms-util-item.active")
    );
  }

  function sync() {
    var n = nav();
    if (!n) return;
    if (!MEASURED && !measure()) { schedule(); return; }

    /* Lesephase: genau eine Layout-Abfrage. */
    var avail = n.clientWidth - 1; // 1px Reserve gegen Subpixel-Rundung
    var act = activeIndex();
    var p = plan(avail, act);
    var sig = (p.compact ? "c" : "n") + "|" + p.keep.join(",") + "|" + act;

    syncUtilMarker();
    /* Schutz gegen ResizeObserver-Rückkopplung: unveränderter Zustand
       bedeutet null DOM-Schreibzugriffe, der Observer läuft sich tot.
       (Strukturell kann er ohnehin nicht schwingen, weil .ms-nav flex: 1
       mit min-width: 0 ist und seine Breite nicht vom Inhalt abhängt —
       diese Prüfung ist die zweite Verteidigungslinie.) */
    if (sig === LAST_SIG) return;
    var changed = LAST_SIG !== "";
    LAST_SIG = sig;

    /* Schreibphase: keine Layout-Abfragen mehr. */
    var ls = navLinks(), i, overflow = [], vis = {};
    for (i = 0; i < p.keep.length; i++) vis[p.keep[i]] = true;
    for (i = 0; i < ls.length; i++) {
      var hidden = p.compact || !vis[i];
      ls[i].classList.toggle("is-overflowed", hidden);
      if (hidden) overflow.push(ls[i]);
    }
    n.classList.toggle("is-compact", p.compact);
    renderPanel(overflow, act >= 0 ? ls[act] : null, p.compact);
    var box = moreBox();
    if (box) box.classList.toggle("is-hidden", overflow.length === 0);
    /* Offenes Menü schliessen, sobald sich die Aufteilung ändert — sonst
       zeigt es veraltete Einträge oder der Fokus landet im Nichts. */
    if (changed && moreMenu && moreMenu.isOpen()) moreMenu.close(false);
  }

  function schedule() {
    if (RAF) return;
    RAF = window.requestAnimationFrame(function () { RAF = 0; sync(); });
  }

  function remeasure() {
    MEASURED = false;
    LAST_SIG = "";
    schedule();
  }

  function boot() {
    if (!nav() || !moreBox() || !document.getElementById("ms-util")) return false;

    moreMenu = createMenu("ms-nav-more", "ms-nav-more-btn", "ms-nav-more-panel", ".ms-nav-more-item");
    createMenu("ms-util", "ms-util-btn", "ms-util-panel", ".ms-util-item");

    if (window.ResizeObserver) {
      /* Ein einziger Observer genügt: .ms-nav ist flex: 1, seine Breite
         ändert sich also sowohl beim Fenster-Resize als auch dann, wenn
         der Agenten-Chip rechts erscheint oder verschwindet. */
      new ResizeObserver(schedule).observe(nav());
    } else {
      window.addEventListener("resize", schedule);
    }

    /* Inter lädt von Google Fonts nach; vor dem Swap gemessene Breiten sind
       zu schmal. Nach dem Swap einmal neu messen. */
    if (document.fonts && document.fonts.ready && document.fonts.ready.then) {
      document.fonts.ready.then(remeasure);
    } else {
      window.addEventListener("load", remeasure);
    }

    schedule();
    return true;
  }

  /* Die Kopfzeile rendert React erst nach diesem Skript — auf ihr Erscheinen
     warten (gleiches Muster wie schedulInitialSync weiter oben). */
  function bootWhenReady() {
    if (boot()) return;
    var obs = new MutationObserver(function () { if (boot()) obs.disconnect(); });
    obs.observe(document.body || document.documentElement, {
      childList: true,
      subtree: true,
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootWhenReady);
  } else {
    bootWhenReady();
  }

  window.msNavOverflow = { sync: schedule, remeasure: remeasure };
})();
