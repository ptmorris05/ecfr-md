// Reading preferences, applied before the page paints so nothing flashes.
(function () {
  var scales = [0.86, 0.93, 1, 1.08, 1.17, 1.27];
  var root = document.documentElement;
  var prefs = {};
  try {
    prefs = JSON.parse(localStorage.getItem("ecfr-prefs") || "{}") || {};
  } catch (e) {}
  if (prefs.theme === "light" || prefs.theme === "dark") root.dataset.theme = prefs.theme;
  if (typeof prefs.size === "number") root.style.setProperty("--scale", scales[prefs.size] || 1);
  if (prefs.dense) root.classList.add("dense");
  if (prefs.rail) root.classList.add("rail");
  if (prefs.focus) root.classList.add("focus");
  window.ecfrScales = scales;
})();
