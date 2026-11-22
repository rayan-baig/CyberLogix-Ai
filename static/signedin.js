/* The public pages' "Sign in" button, for somebody who is signed in
 * already: it says "Your console" instead (same address). Read from the
 * session the console and the sign-up page keep in this browser; nothing
 * is sent anywhere. Storage can be refused (a private window), and then
 * the button simply stays "Sign in".
 */
"use strict";
(function () {
  let signedIn = false;
  try { signedIn = Boolean(window.localStorage.getItem("cyberlogix.session")); } catch (_) {}
  if (!signedIn) return;
  document.querySelectorAll('header a[href="/console"]').forEach((link) => {
    if (link.textContent.trim() !== "Sign in") return;
    link.textContent = "Your console";
    link.setAttribute("aria-label", "Your console: you are signed in");
  });
})();
