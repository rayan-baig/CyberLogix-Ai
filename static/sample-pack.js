"use strict";
/* The sample insurance pack's one button (3.35.0): print it, or save it
   as a PDF, as an adjuster would. */
(function () {
  const button = document.getElementById("sample-print");
  if (button) button.addEventListener("click", () => window.print());
})();
