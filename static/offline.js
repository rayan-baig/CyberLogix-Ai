"use strict";
// Come back by itself the moment the network returns, so somebody
// staring at this screen is not the thing that has to notice.
addEventListener("online", () => location.reload());

// The button's handler lives here rather than in an onclick attribute: the
// Content-Security-Policy refuses inline handlers along with inline script.
document.getElementById("retry").addEventListener("click", () => location.reload());
