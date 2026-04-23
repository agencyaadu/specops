/*
 * Shared theme bootstrap.
 * - Synchronous IIFE at the top sets data-theme on <html> BEFORE the body
 *   paints, so there's no flash of the wrong mode.
 * - addButton() runs after DOM is ready and injects a floating Light/Dark
 *   toggle, bottom-right, that persists the choice to localStorage.
 */
(function () {
  var KEY  = "spec-theme";
  var root = document.documentElement;

  function currentMode() {
    return root.getAttribute("data-theme") === "light" ? "light" : "dark";
  }
  function apply(mode, persist) {
    if (mode === "light") root.setAttribute("data-theme", "light");
    else                  root.removeAttribute("data-theme");
    if (persist !== false) {
      try { localStorage.setItem(KEY, mode); } catch (_) {}
    }
  }

  // Resolve initial theme: explicit user choice > OS preference > dark default.
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (_) {}
  var prefersLight = false;
  try { prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches; } catch (_) {}
  apply(saved || (prefersLight ? "light" : "dark"), /* persist */ !!saved);

  function addButton() {
    if (document.querySelector(".theme-fab")) return;
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "theme-fab";
    btn.setAttribute("aria-label", "Toggle light/dark theme");
    function refresh() {
      btn.textContent = currentMode() === "light" ? "Dark mode" : "Light mode";
    }
    refresh();
    btn.addEventListener("click", function () {
      apply(currentMode() === "light" ? "dark" : "light");
      refresh();
    });
    document.body.appendChild(btn);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", addButton);
  } else {
    addButton();
  }
})();
