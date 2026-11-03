"use strict";

const $ = (id) => document.getElementById(id);

function say(which, message) {
  const other = which === "good" ? "bad" : "good";
  $(other).hidden = true;
  $(which).hidden = false;
  // textContent, not innerHTML: this carries a server message and, on the
  // failure path, whatever the browser said went wrong.
  $(which).textContent = message;
}

async function detail(resp) {
  try {
    const body = await resp.json();
    return typeof body.detail === "string" ? body.detail : null;
  } catch (err) {
    return null;
  }
}

/* The token arrives in the query string. It is read once and the URL is
   scrubbed straight away, so a one-time credential does not sit in the
   address bar to be shoulder-read, pasted into a support chat, or handed
   to the next site in a Referer header. */
const params = new URLSearchParams(location.search);
const token = (params.get("token") || "").trim();
if (token) {
  history.replaceState(null, "", location.pathname);
  $("set").hidden = false;
} else {
  $("ask").hidden = false;
}

$("ask").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("ask-go").disabled = true;
  try {
    const resp = await fetch("/api/accounts/forgot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("email").value.trim() }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(body.detail || `HTTP ${resp.status}`);
    $("ask").hidden = true;
    say("good", body.message);
  } catch (err) {
    $("ask-go").disabled = false;
    say("bad", `That did not go through (${err.message}).`);
  }
});

$("set").addEventListener("submit", async (event) => {
  event.preventDefault();
  if ($("password").value !== $("confirm").value) {
    say("bad", "Those two do not match.");
    return;
  }
  $("set-go").disabled = true;
  try {
    const resp = await fetch("/api/accounts/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, new_password: $("password").value }),
    });
    if (!resp.ok) {
      const said = await detail(resp);
      $("set-go").disabled = false;
      say("bad", said || `That did not work (HTTP ${resp.status}).`);
      return;
    }
    $("set").hidden = true;
    say("good", "Password set. Sign in with it at /console.");
  } catch (err) {
    $("set-go").disabled = false;
    say("bad", `That did not go through (${err.message}).`);
  }
});
