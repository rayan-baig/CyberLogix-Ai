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
      afterClose(dialog.dataset.id, got.body, (got.body && got.body.message) || "Closed.");
    });
    return dialog;
  }

  /* "Food kept" or "Thrown away" (3.28.0): asked once an alert closes,
     because it decides whether the alert says "Food protected" or "Food
     at risk: check it" (food_value.py). "Not sure yet" leaves it to be
     answered later from the alert. */
  let foodDialog = null;
  function buildFood() {
    if (foodDialog) return foodDialog;
    const kept = el("button", { class: "btn btn-primary", type: "button", id: "food-kept",
      "data-food": "kept", text: "Food kept" });
    const thrown = el("button", { class: "btn", type: "button", id: "food-thrown",
      "data-food": "thrown", text: "Thrown away" });
    const later = el("button", { class: "btn linkish", type: "button", id: "food-later",
      text: "Not sure yet" });
    foodDialog = el("dialog", { class: "confirm-dialog food-dialog", id: "food-dialog",
      "aria-labelledby": "food-title" }, [
      el("h2", { class: "confirm-title", id: "food-title", text: "Closed. What about the food?" }),
      el("p", { class: "fix-sub", id: "food-sub",
        text: "One tap, for the record. It goes in the log with your name." }),
      el("p", { class: "confirm-out", id: "food-out", role: "status" }),
      el("div", { class: "confirm-acts food-acts" }, [kept, thrown, later]),
    ]);
    document.body.appendChild(foodDialog);
    foodDialog.addEventListener("click", async (event) => {
      const b = event.target.closest("button");
      if (!b) return;
      if (b === later) {
        foodDialog.close();
        cfg.say(foodDialog.dataset.closed || "Closed.", "ok");
        cfg.done();
        return;
      }
      if (!b.dataset.food) return;
      const ok = await food(foodDialog.dataset.id, b.dataset.food, b, true);
      if (ok) foodDialog.close();
    });
    return foodDialog;
  }

  function afterClose(id, body, closedWords) {
    const ask = body && body.ask_food;
    if (!ask) { cfg.say(closedWords, "ok"); cfg.done(); return; }
    const d = buildFood();
    d.dataset.id = id;
    d.dataset.closed = closedWords;
    const word = ask.word === "stock" ? "stock" : "food";
    d.querySelector("#food-title").textContent = `Closed. What about the ${word}?`;
    d.querySelector("#food-kept").textContent = word === "stock" ? "Stock kept" : "Food kept";
    d.querySelector("#food-out").textContent = "";
    if (typeof d.showModal === "function") d.showModal(); else d.setAttribute("open", "");
  }

  /* Say what happened to the food, from the dialog or from the alert's
     own "Food at risk: check it" buttons. One message, then a fresh look. */
  async function food(id, answerWord, button, fromDialog) {
    if (button) button.disabled = true;
    let got = null;
    try {
      got = await post(`/api/voice/food/${encodeURIComponent(id)}`, { food: answerWord });
    } catch (_) { got = null; }
    if (button) button.disabled = false;
    if (!got || !got.ok) {
      const msg = got ? words(got.body, "That did not go through. Try again.")
        : "No connection. Nothing was changed.";
      if (fromDialog && foodDialog) foodDialog.querySelector("#food-out").textContent = msg;
      else cfg.say(msg, "err");
      return false;
    }
    const closed = fromDialog && foodDialog ? `${foodDialog.dataset.closed || "Closed."} ` : "";
    cfg.say(closed + ((got.body && got.body.message) || "Noted."), "ok");
    cfg.done();
    return true;
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
      cfg.done();
      return;
    }
    afterClose(id, got.body, "Closed. Thanks for sorting it.");
  }

  root.CLXAnswer = Object.freeze({
    setup(options) { cfg = Object.assign(cfg, options || {}); },
    onIt: (id, button) => answer("ack", id, button),
    fixed: (id, button) => answer("fix", id, button),
    food: (id, answerWord, button) => food(id, answerWord, button, false),
  });
})(typeof window !== "undefined" ? window : globalThis);
