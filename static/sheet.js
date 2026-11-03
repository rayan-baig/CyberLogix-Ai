"use strict";

/* The one-page sales sheet: the print button, and nothing else. Every
 * word and figure on the sheet is rendered by the server (salessheet.py). */
(function () {
  const button = document.getElementById("print-sheet");
  if (button) button.addEventListener("click", () => window.print());
})();
