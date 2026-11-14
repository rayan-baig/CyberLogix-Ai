"use strict";

/* The partner application form (/insurance-partners). One POST; the
   answer is the same whether or not this address applied before. */

const $ = (id) => document.getElementById(id);

$("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("error").hidden = true;
  const payload = {};
  for (const name of ["kind", "company_name", "contact_name", "contact_email",
                      "contact_phone", "website", "message"]) {
    payload[name] = $(name).value.trim();
  }
  $("go").disabled = true;
  try {
    const resp = await fetch("/api/insurance-partners/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await resp.json().catch(() => ({}));
    if (resp.ok) {
      $("form").hidden = true;
      $("done").hidden = false;
      $("done").textContent = body.message || "Thank you. We will reply by email.";
      return;
    }
    $("error").hidden = false;
    $("error").textContent = typeof body.detail === "string"
      ? body.detail
      : "Please check each field: a company, your name and a work email are needed.";
  } catch (_) {
    $("error").hidden = false;
    $("error").textContent = "That could not be sent. Please try again in a minute.";
  } finally {
    $("go").disabled = false;
  }
});
