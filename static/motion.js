/* Motion for the public pages. Pairs with motion.css.
 *
 * Usage:
 *   <link rel="stylesheet" href="/static/motion.css">
 *   <script src="/static/motion.js" defer></script>
 *
 * Nothing happens for anybody who asked for less movement: the class
 * that switches the stylesheet on is never added, and the page stays
 * still. Everything here only adds to a page that already reads fine
 * without it -- no text exists only in a script, and nothing is hidden
 * unless this file is the one that will also reveal it.
 *
 * Content that arrives later (the sector and plan grids are fetched)
 * is picked up by a MutationObserver, so the page's own rendering code
 * does not need to know this file exists.
 */
(function () {
  "use strict";

  if (!window.matchMedia || !("IntersectionObserver" in window)) return;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  var root = document.documentElement;
  root.classList.add("motion");

  // ---- the headline, split into words that rise in one after another ----
  function splitWords(el) {
    if (!el || el.dataset.split) return;
    el.dataset.split = "1";
    var i = 0;
    Array.prototype.slice.call(el.childNodes).forEach(function (node) {
      if (node.nodeType === 3) {
        var frag = document.createDocumentFragment();
        node.textContent.split(/(\s+)/).forEach(function (part) {
          if (!part) return;
          if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); return; }
          var span = document.createElement("span");
          span.className = "w";
          span.style.setProperty("--i", i++);
          span.textContent = part;
          frag.appendChild(span);
        });
        node.parentNode.replaceChild(frag, node);
      } else if (node.nodeType === 1 && node.tagName !== "BR") {
        node.classList.add("w");
        node.style.setProperty("--i", i++);
      }
    });
  }

  // ---- the kicker, typed out; the full words stay readable to a screen reader ----
  function typeOut(el) {
    if (!el || el.dataset.typed) return;
    el.dataset.typed = "1";
    var text = el.textContent;
    el.setAttribute("aria-label", text);
    el.textContent = "";
    el.classList.add("typing");
    var n = 0;
    (function tick() {
      n += 1;
      el.textContent = text.slice(0, n);
      if (n < text.length) setTimeout(tick, 28 + Math.random() * 40);
      else el.classList.add("typed");
    })();
  }

  // ---- a number that counts up to itself when it scrolls into view ----
  function countUp(el) {
    var match = el.textContent.match(/^(\D*)([\d,]+(?:\.\d+)?)(.*)$/);
    if (!match) return;
    var prefix = match[1], target = parseFloat(match[2].replace(/,/g, "")), suffix = match[3];
    if (!(target > 0)) return;
    var start = null, ms = 1100;
    function frame(t) {
      if (start === null) start = t;
      var p = Math.min(1, (t - start) / ms);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = prefix + Math.round(target * eased).toLocaleString("en-US") + suffix;
      if (p < 1) requestAnimationFrame(frame);
      else el.textContent = prefix + match[2] + suffix;
    }
    requestAnimationFrame(frame);
  }

  // ---- things that slide in as they scroll into view ----
  var seen = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (!e.isIntersecting) return;
      e.target.classList.add("in");
      seen.unobserve(e.target);
      Array.prototype.forEach.call(
        e.target.querySelectorAll("[data-count]"), countUp);
    });
  }, { rootMargin: "0px 0px -8% 0px", threshold: 0.12 });

  // The home page, the sign-up page and the industry pages share this
  // file, so each list names the pieces of all three. A selector that
  // matches nothing on a page costs nothing there.
  var HEADLINE = ".hero h1, main > section > h1, #page > h1";
  var TYPED = ".hero .kicker, #page > .eyebrow";
  var REVEAL = [
    ".band-head h2", ".band-head p", ".closer h2", ".closer p",
    ".ladder", ".sector", ".plan", ".proof-item", ".cta-row",
    ".lede:not(h1)", ".points li", "#panel",
    "#page > .standfirst", ".slab", ".does li", ".ind", ".foot"
  ].join(",");
  var GLOW = ".sector, .plan, .rung, .ind";

  function prepare(scope) {
    Array.prototype.forEach.call(scope.querySelectorAll(HEADLINE), splitWords);
    Array.prototype.forEach.call(scope.querySelectorAll(TYPED), typeOut);
    Array.prototype.forEach.call(scope.querySelectorAll(REVEAL), function (el) {
      if (el.hasAttribute("data-reveal")) return;
      el.setAttribute("data-reveal", "");
      // stagger cards against their siblings, capped so the tenth card
      // does not wait a second and a half
      var siblings = el.parentElement ? el.parentElement.children : [];
      var idx = Array.prototype.indexOf.call(siblings, el);
      el.style.setProperty("--d", Math.min(Math.max(idx, 0), 6));
      seen.observe(el);
    });
    Array.prototype.forEach.call(scope.querySelectorAll(GLOW), function (el) {
      el.classList.add("glow");
    });
    Array.prototype.forEach.call(scope.querySelectorAll(".sector-price b"), function (el) {
      el.setAttribute("data-count", "");
    });
  }

  // the pointer light, one listener for the whole page
  document.addEventListener("pointermove", function (e) {
    var card = e.target.closest && e.target.closest(".glow");
    if (!card) return;
    var r = card.getBoundingClientRect();
    card.style.setProperty("--mx", (e.clientX - r.left) + "px");
    card.style.setProperty("--my", (e.clientY - r.top) + "px");
  }, { passive: true });

  function start() {
    // what is on screen already comes in straight away, just after the
    // headline, rather than waiting for a scroll
    prepare(document);
    new MutationObserver(function (records) {
      records.forEach(function (r) {
        Array.prototype.forEach.call(r.addedNodes, function (n) {
          if (n.nodeType === 1) prepare(n.parentElement || n);
        });
      });
    }).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
