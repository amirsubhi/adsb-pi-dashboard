// Theme for every page: Auto follows the device; Light or Dark overrides it
// and is remembered in this browser (shared by all pages of the dashboard).
// Loaded in <head> so the saved choice applies before the first paint.
(function () {
  var KEY = "adsb-theme";
  var root = document.documentElement;
  function saved() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function apply(t) {
    if (t === "light" || t === "dark") root.dataset.theme = t; else delete root.dataset.theme;
  }
  apply(saved());

  document.addEventListener("DOMContentLoaded", function () {
    var buttons = document.querySelectorAll(".theme button[data-t]");
    function mark(t) {
      buttons.forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.t === t)); });
    }
    mark(root.dataset.theme || "auto");
    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        var t = b.dataset.t;
        apply(t);
        try { if (t === "auto") localStorage.removeItem(KEY); else localStorage.setItem(KEY, t); } catch (e) {}
        mark(t);
        document.dispatchEvent(new CustomEvent("themechange"));
      });
    });
  });
})();
