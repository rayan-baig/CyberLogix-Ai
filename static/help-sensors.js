// /help/sensors: each sensor guide is a fold (sensor_guides.render_html).
// A link to one, such as /help/sensors#monnit, opens it, on arrival and
// whenever the address's #part changes; opening one by hand puts its name
// in the address, so it can be copied and sent. On a wide screen "What you
// need" opens by itself; on a phone it waits behind its "More".
(function () {
  "use strict";
  let wide = true;
  try { wide = window.matchMedia("(min-width: 721px)").matches; } catch (_) { wide = true; }
  if (wide) document.querySelectorAll("details[data-open-wide]").forEach((d) => { d.open = true; });

  function openFor() {
    let id = "";
    try { id = decodeURIComponent((window.location.hash || "").slice(1)); } catch (_) { return; }
    const target = id ? document.getElementById(id) : null;
    if (!target) return;
    const fold = target.tagName === "DETAILS" ? target : target.closest("details");
    if (!fold) return;
    // The fold the link names, and any fold around it.
    for (let d = fold; d; d = d.parentElement && d.parentElement.closest("details")) d.open = true;
    land(target);
  }

  // The page's fonts, and Pilot's room at the sides of a phone screen,
  // arrive after this runs and push the guide down. For a few seconds,
  // land on it again each time the page's size changes, unless the reader
  // has scrolled since.
  let watch = null;
  function land(target) {
    target.scrollIntoView();
    let y = window.scrollY;
    if (watch) watch.disconnect();
    if (typeof ResizeObserver !== "function") return;
    watch = new ResizeObserver(() => {
      if (window.scrollY !== y) { watch.disconnect(); return; }
      target.scrollIntoView();
      y = window.scrollY;
    });
    watch.observe(document.body);
    const w = watch;
    setTimeout(() => w.disconnect(), 4000);
  }

  document.querySelectorAll("details.guide[id]").forEach((d) => {
    d.addEventListener("toggle", () => {
      if (!d.open || window.location.hash === `#${d.id}`) return;
      try { window.history.replaceState(null, "", `#${d.id}`); } catch (_) { /* a file:// copy */ }
    });
  });
  openFor();
  window.addEventListener("hashchange", openFor);
})();
