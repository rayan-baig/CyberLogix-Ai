"use strict";

/* "I'm on it" and "It's fixed", the same on every page that offers them.

   "It's fixed" used to close an alert while the fridge was still 48.4°F,
   with no question and no note, and a new alert texted everybody again
   17 seconds later. Now the server answers a unit still out of range with
   what it is ("It's still 48°F. We'll close it when it's back under
   41°F."), keeps it open, and closes it by itself once it is cold. To
   close it anyway takes one line saying why, picked from the usual
   reasons or written: that line, and who wrote it, is what an inspector
   reads.

   Built with the DOM, no markup strings and no inline style, so it runs
   under the page's strict policy. Each page hands over how to read its
   session and what to do once something changed. */
(function (root) {
  let cfg = { token: () => null, done: () => {}, say: () => {} };
  let dialog = null;

  function el(tag, attrs, kids) {
    const node = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (k === "text") node.textContent = v;
      else if (v !== null && v !== undefined && v !== false) node.setAttribute(k, v === true ? "" : v);
    });
    (kids || []).forEach((kid) => node.appendChild(kid));
    return node;
  }

  async function post(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 Authorization: "Bearer " + (cfg.token() || "") },
      body: JSON.stringify(body || {}),
    });
    let data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    return { ok: res.ok, status: res.status, body: data };
  }

  function words(body, fallback) {
    const d = body && body.detail;
    if (typeof d === "string") return d;
    if (d && typeof d.message === "string") return d.message;
    if (Array.isArray(d) && d.length && d[0].msg) return String(d[0].msg).replace(/^Value error, /, "");
    return fallback;
  }

  function build() {
    if (dialog) return dialog;
    const note = el("input", { id: "fix-note", type: "text", maxlength: "200",
      autocomplete: "off", placeholder: "Or write one line: what was done" });
    const reasons = el("div", { class: "fix-reasons", id: "fix-reasons", role: "group",
      "aria-label": "Common reasons" });
    const closeBtn = el("button", { class: "btn btn-danger-soft", type: "button",
      id: "fix-close", disabled: true, text: "Close anyway" });
    const keep = el("button", { class: "btn btn-primary", type: "button", id: "fix-keep",
      text: "OK, keep watching it" });
    dialog = el("dialog", { class: "confirm-dialog fix-dialog", id: "fix-dialog",
      "aria-labelledby": "fix-title" }, [
      el("h2", { class: "confirm-title", id: "fix-title", text: "Not back in range yet" }),
      el("p", { class: "fix-msg", id: "fix-msg" }),
      el("p", { class: "fix-sub", text: "You have it now, so nobody else is called about it. "
        + "There is nothing more to press: it closes by itself once it is cold." }),
      el("div", { class: "confirm-acts" }, [keep]),
      el("details", { class: "fix-anyway", id: "fix-anyway" }, [
        el("summary", { text: "Close it anyway" }),
        el("p", { class: "fix-sub", text: "Say why in a line. It goes in the record with your name, "
          + "for an inspector. If it is still out of range after its wait, we alert again." }),
        reasons,
        el("label", { class: "sr", for: "fix-note", text: "What was done" }),
        note,
        el("p", { class: "confirm-out", id: "fix-out", role: "status" }),
        el("div", { class: "confirm-acts" }, [closeBtn]),
      ]),
    ]);
    document.body.appendChild(dialog);
    const ready = () => { closeBtn.disabled = note.value.trim().length < 3; };
    note.addEventListener("input", () => {
      reasons.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", "false"));
      ready();
    });
    reasons.addEventListener("click", (event) => {
      const b = event.target.closest("button");
      if (!b) return;
      reasons.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      note.value = b.textContent;
      ready();
    });
    keep.addEventListener("click", () => { dialog.close(); cfg.done(); });
    closeBtn.addEventListener("click", async () => {
      closeBtn.disabled = true;
      const got = await post(`/api/voice/resolve/${encodeURIComponent(dialog.dataset.id)}`,
        { close_anyway: true, note: note.value.trim() }).catch(() => null);
      if (!got || !got.ok) {
        dialog.querySelector("#fix-out").textContent =
          words(got && got.body, "That did not go through. Try again.");
        ready();
        return;
      }
      dialog.close();
      cfg.say((got.body && got.body.message) || "Closed.");
      cfg.done();
    });
    return dialog;
  }

  function showStillOut(id, detail) {
    const d = build();
    d.dataset.id = id;
    d.querySelector("#fix-msg").textContent = detail.message;
    d.querySelector("#fix-out").textContent = "";
    d.querySelector("#fix-note").value = "";
    d.querySelector("#fix-close").disabled = true;
    d.querySelector("#fix-anyway").open = false;
    const box = d.querySelector("#fix-reasons");
    box.replaceChildren(...(detail.reasons || []).map((r) =>
      el("button", { class: "f-tab", type: "button", "aria-pressed": "false", text: r })));
    if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
  }

  /* Either button: returns once the page should look again. */
  async function answer(kind, id, button) {
    if (button) button.disabled = true;
    const path = kind === "ack" ? `/api/voice/acknowledge/${encodeURIComponent(id)}`
                                : `/api/voice/resolve/${encodeURIComponent(id)}`;
    let got = null;
    try { got = await post(path, {}); } catch (_) { got = null; }
    if (button) button.disabled = false;
    if (!got) { cfg.say("No connection. Nothing was changed.", "err"); return; }
    if (got.status === 409 && got.body && got.body.detail
        && got.body.detail.code === "still_out_of_range") {
      showStillOut(id, got.body.detail);
      return;
    }
    if (!got.ok) { cfg.say(words(got.body, "That did not go through."), "err"); return; }
    if (kind === "ack") {
      cfg.say(got.body && got.body.status === "ALREADY_ACKNOWLEDGED"
        ? got.body.message : "Thanks. Everyone can see you are on it.", "ok");
    } else {
      cfg.say("Closed. Thanks for sorting it.", "ok");
    }
    cfg.done();
  }

  root.CLXAnswer = Object.freeze({
    setup(options) { cfg = Object.assign(cfg, options || {}); },
    onIt: (id, button) => answer("ack", id, button),
    fixed: (id, button) => answer("fix", id, button),
  });
})(typeof window !== "undefined" ? window : globalThis);
