"use strict";

(() => {
  const key = "stockweight-theme";
  let theme = "dark";
  try {
    const saved = localStorage.getItem(key);
    if (saved === "light" || saved === "dark") theme = saved;
  } catch {
    // The current theme remains usable when browser storage is unavailable.
  }
  function applyTheme(persist = false) {
    const dark = theme === "dark";
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]').content = dark ? "#24292e" : "#30363b";
    const toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.setAttribute("aria-checked", String(dark));
      document.getElementById("theme-label").textContent = dark ? "Noturno" : "Diurno";
    }
    if (persist) {
      try { localStorage.setItem(key, theme); } catch { /* Storage may be disabled. */ }
    }
    window.dispatchEvent(new Event("themechange"));
  }
  applyTheme();
  document.addEventListener("DOMContentLoaded", () => {
    applyTheme();
    document.getElementById("theme-toggle").addEventListener("click", () => {
      theme = theme === "dark" ? "light" : "dark";
      applyTheme(true);
    });
  });
})();
