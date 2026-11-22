// Pilot's shared core, run in Node: `node --test tests/js/pilot.test.js`.
// tests/test_pilot.py runs this file too, so `python -m pytest` covers it.
// Every provider is mocked: no request leaves this machine.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");

const core = (f) => path.join(__dirname, "..", "..", "static", "pilot", f);
const app = (f) => path.join(__dirname, "..", "..", "static", f);
// The core files find each other on the global object, as in a browser.
const Store = require(core("pilot-store.js"));
const Providers = require(core("pilot-providers.js"));
globalThis.PilotProviders = Providers;
const Engine = require(core("pilot-engine.js"));
const Render = require(core("pilot-render.js"));
const Media = require(core("pilot-media.js"));
const Mascot = require(core("pilot-mascot.js"));

function memoryStorage(data = new Map()) {
  return {
    data,
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => { data.set(k, String(v)); },
    removeItem: (k) => { data.delete(k); },
  };
}
const blocked = { getItem() { throw new Error("no"); }, setItem() { throw new Error("no"); }, removeItem() { throw new Error("no"); } };

// A fake fetch that answers from a list of handlers and records each call.
function fakeFetch(handler) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init, body: init && init.body ? JSON.parse(init.body) : null });
    return handler(url, init, calls.length);
  };
  fn.calls = calls;
  return fn;
}
const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
function sseResponse(chunks) {
  const enc = new TextEncoder();
  return new Response(new ReadableStream({
    start(c) { for (const ch of chunks) c.enqueue(enc.encode(ch)); c.close(); },
  }), { status: 200, headers: { "content-type": "text/event-stream" } });
}
const ev = (obj, name) => `${name ? `event: ${name}\n` : ""}data: ${JSON.stringify(obj)}\n\n`;

const ANTHROPIC = { provider: "anthropic", key: "test-key", model: "test-model", maxTokens: 0 };

// ---------------------------------------------------------------- providers

test("Anthropic: the browser-access header, the key, and a streamed answer", async () => {
  const fetcher = fakeFetch(() => sseResponse([
    ev({ type: "message_start", message: { id: "m" } }, "message_start"),
    ev({ type: "content_block_start", index: 0, content_block: { type: "text", text: "" } }, "content_block_start"),
    // split across chunks on purpose
    "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,",
    "\"delta\":{\"type\":\"text_delta\",\"text\":\"Hel\"}}\n\n",
    ev({ type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "lo" } }, "content_block_delta"),
    ev({ type: "content_block_stop", index: 0 }, "content_block_stop"),
    ev({ type: "message_delta", delta: { stop_reason: "end_turn" } }, "message_delta"),
  ]));
  const seen = [];
  const reply = await Providers.chat(ANTHROPIC, { system: "S", messages: [{ role: "user", text: "Hi" }], tools: [], onText: (t) => seen.push(t) },
    { fetch: fetcher });
  assert.equal(reply.text, "Hello");
  assert.equal(reply.stop, "done");
  assert.deepEqual(seen, ["Hel", "Hello"]);
  const call = fetcher.calls[0];
  assert.equal(call.url, "https://api.anthropic.com/v1/messages");
  assert.equal(call.init.headers["anthropic-dangerous-direct-browser-access"], "true");
  assert.equal(call.init.headers["x-api-key"], "test-key");
  assert.ok(call.init.headers["anthropic-version"]);
  assert.equal(call.init.credentials, "omit");
  assert.equal(call.body.model, "test-model");
  assert.equal(call.body.system, "S");
  assert.equal(call.body.stream, true);
  assert.equal(call.body.tools, undefined);
});

test("Anthropic: tool calls are read from the stream, and sent back with their results unchanged", async () => {
  const fetcher = fakeFetch(() => sseResponse([
    ev({ type: "content_block_start", index: 0, content_block: { type: "thinking", thinking: "" } }),
    ev({ type: "content_block_delta", index: 0, delta: { type: "thinking_delta", thinking: "hmm" } }),
    ev({ type: "content_block_delta", index: 0, delta: { type: "signature_delta", signature: "sig123" } }),
    ev({ type: "content_block_stop", index: 0 }),
    ev({ type: "content_block_start", index: 1, content_block: { type: "tool_use", id: "toolu_1", name: "add_step", input: {} } }),
    ev({ type: "content_block_delta", index: 1, delta: { type: "input_json_delta", partial_json: "{\"stage\":\"pl" } }),
    ev({ type: "content_block_delta", index: 1, delta: { type: "input_json_delta", partial_json: "an\",\"step\":\"X\"}" } }),
    ev({ type: "content_block_stop", index: 1 }),
    ev({ type: "message_delta", delta: { stop_reason: "tool_use" } }),
  ]));
  const tools = [{ name: "add_step", description: "d", parameters: { type: "object", properties: {} } }];
  const reply = await Providers.chat(ANTHROPIC, { system: "S", messages: [{ role: "user", text: "Hi" }], tools }, { fetch: fetcher });
  assert.deepEqual(reply.toolCalls, [{ id: "toolu_1", name: "add_step", args: { stage: "plan", step: "X" } }]);
  assert.equal(reply.stop, "tools");
  assert.deepEqual(fetcher.calls[0].body.tools, [{ name: "add_step", description: "d", input_schema: { type: "object", properties: {} } }]);
  // The next request replays the reply exactly, signed thinking included.
  const wire = Providers.adapters.anthropic.toWire([
    { role: "user", text: "Hi" },
    { role: "assistant", text: "", toolCalls: reply.toolCalls, raw: reply.raw },
    { role: "tool", results: [{ id: "toolu_1", name: "add_step", content: "ok" }] },
  ]);
  assert.equal(wire[1].content[0].type, "thinking");
  assert.equal(wire[1].content[0].signature, "sig123");
  assert.deepEqual(wire[1].content[1].input, { stage: "plan", step: "X" });
  assert.deepEqual(wire[2], { role: "user", content: [{ type: "tool_result", tool_use_id: "toolu_1", content: "ok" }] });
});

test("Anthropic: the model list comes from the provider, with each model's output limit", async () => {
  const fetcher = fakeFetch(() => json(200, { data: [{ id: "model-one", display_name: "Model One", max_tokens: 9000 }], has_more: false }));
  const list = await Providers.listModels(ANTHROPIC, { fetch: fetcher });
  assert.deepEqual(list, [{ id: "model-one", label: "Model One", maxTokens: 9000 }]);
  assert.match(fetcher.calls[0].url, /^https:\/\/api\.anthropic\.com\/v1\/models/);
});

test("OpenAI: streamed words and tool calls put together from pieces", async () => {
  const fetcher = fakeFetch(() => sseResponse([
    ev({ choices: [{ delta: { content: "Sure" } }] }),
    ev({ choices: [{ delta: { tool_calls: [{ index: 0, id: "call_1", function: { name: "show_my_projects", arguments: "" } }] } }] }),
    ev({ choices: [{ delta: { tool_calls: [{ index: 0, function: { arguments: "{}" } }] } }] }),
    ev({ choices: [{ delta: {}, finish_reason: "tool_calls" }] }),
    "data: [DONE]\n\n",
  ]));
  const s = { provider: "openai", key: "k", model: "m" };
  const tools = [{ name: "show_my_projects", description: "d", parameters: { type: "object", properties: {} } }];
  const reply = await Providers.chat(s, { system: "S", messages: [{ role: "user", text: "Hi" }], tools }, { fetch: fetcher });
  assert.equal(reply.text, "Sure");
  assert.deepEqual(reply.toolCalls, [{ id: "call_1", name: "show_my_projects", args: {} }]);
  const call = fetcher.calls[0];
  assert.equal(call.url, "https://api.openai.com/v1/chat/completions");
  assert.equal(call.init.headers.authorization, "Bearer k");
  assert.deepEqual(call.body.messages[0], { role: "system", content: "S" });
  assert.equal(call.body.tools[0].type, "function");
  const wire = Providers.adapters.openai.toWire("S", [{ role: "user", text: "Hi" },
    { role: "assistant", text: "Sure", toolCalls: reply.toolCalls }, { role: "tool", results: [{ id: "call_1", content: "none" }] }]);
  assert.equal(wire[2].tool_calls[0].function.arguments, "{}");
  assert.deepEqual(wire[3], { role: "tool", tool_call_id: "call_1", content: "none" });
});

test("OpenAI: the model list leaves out models that don't chat", async () => {
  const fetcher = fakeFetch(() => json(200, { data: [{ id: "chat-model" }, { id: "text-embedding-thing" }, { id: "some-tts-voice" }] }));
  const list = await Providers.listModels({ provider: "openai", key: "k" }, { fetch: fetcher });
  assert.deepEqual(list.map((m) => m.id), ["chat-model"]);
});

test("Gemini: the key in its header, function calls, and their signatures kept for the reply", async () => {
  const fetcher = fakeFetch(() => sseResponse([
    ev({ candidates: [{ content: { role: "model", parts: [{ text: "Let me look." }] } }] }),
    ev({ candidates: [{ content: { role: "model", parts: [{ functionCall: { name: "whats_my_next_step", args: {} }, thoughtSignature: "abc" }] },
      finishReason: "STOP" }] }),
  ]));
  const s = { provider: "gemini", key: "g-key", model: "a-model" };
  const tools = [{ name: "whats_my_next_step", description: "d", parameters: { type: "object", properties: {} } }];
  const reply = await Providers.chat(s, { system: "S", messages: [{ role: "user", text: "Hi" }], tools }, { fetch: fetcher });
  const call = fetcher.calls[0];
  assert.equal(call.url, "https://generativelanguage.googleapis.com/v1beta/models/a-model:streamGenerateContent?alt=sse");
  assert.equal(call.init.headers["x-goog-api-key"], "g-key");
  assert.ok(!call.url.includes("g-key"), "the key never goes in the address");
  assert.deepEqual(call.body.systemInstruction, { parts: [{ text: "S" }] });
  assert.equal(call.body.tools[0].functionDeclarations[0].name, "whats_my_next_step");
  assert.equal(reply.text, "Let me look.");
  assert.equal(reply.toolCalls[0].name, "whats_my_next_step");
  const wire = Providers.adapters.gemini.toWire([{ role: "user", text: "Hi" },
    { role: "assistant", text: reply.text, toolCalls: reply.toolCalls, raw: reply.raw },
    { role: "tool", results: [{ id: reply.toolCalls[0].id, name: "whats_my_next_step", content: "Tick a box" }] }]);
  assert.equal(wire[1].role, "model");
  assert.equal(wire[1].parts[1].thoughtSignature, "abc");
  assert.deepEqual(wire[2].parts[0].functionResponse, { name: "whats_my_next_step", response: { result: "Tick a box" } });
});

test("an OpenAI-compatible address works only for the hosts the app allows", async () => {
  const hosts = ["openrouter.ai", "localhost"];
  assert.equal(Providers.checkBaseUrl("https://openrouter.ai/api/v1", hosts), null);
  assert.equal(Providers.checkBaseUrl("http://localhost:11434/v1", hosts), null);
  assert.equal(Providers.checkBaseUrl("http://openrouter.ai/api/v1", hosts).kind, "address");
  assert.equal(Providers.checkBaseUrl("https://elsewhere.example/v1", hosts).kind, "address");
  assert.equal(Providers.checkBaseUrl("https://user:pw@openrouter.ai/v1", hosts).kind, "address");
  assert.equal(Providers.checkBaseUrl("not a url", hosts).kind, "address");
  const fetcher = fakeFetch(() => json(200, { data: [] }));
  await assert.rejects(Providers.chat({ provider: "compatible", baseUrl: "https://elsewhere.example/v1", model: "m" },
    { system: "S", messages: [] }, { fetch: fetcher, compatibleHosts: hosts }), (e) => e.kind === "address");
  assert.equal(fetcher.calls.length, 0, "nothing is sent to an address that isn't allowed");
  // A local model needs no key.
  const local = fakeFetch(() => sseResponse([ev({ choices: [{ delta: { content: "hi" }, finish_reason: "stop" }] })]));
  const r = await Providers.chat({ provider: "compatible", baseUrl: "http://localhost:11434/v1", model: "m", key: "" },
    { system: "S", messages: [{ role: "user", text: "x" }] }, { fetch: local, compatibleHosts: hosts });
  assert.equal(r.text, "hi");
  assert.equal(local.calls[0].url, "http://localhost:11434/v1/chat/completions");
  assert.equal(local.calls[0].init.headers.authorization, undefined);
});

test("errors are plain and friendly", async () => {
  const refused = fakeFetch(() => json(401, { type: "error", error: { type: "authentication_error", message: "invalid x-api-key" } }));
  await assert.rejects(Providers.chat(ANTHROPIC, { system: "S", messages: [] }, { fetch: refused }),
    (e) => e.kind === "key" && e.message === "Your key was refused by Anthropic. Check it in Pilot settings.");
  const gemini = fakeFetch(() => json(400, { error: { code: 400, message: "API key not valid. Please pass a valid API key.", status: "INVALID_ARGUMENT" } }));
  await assert.rejects(Providers.chat({ provider: "gemini", key: "x", model: "m" }, { system: "S", messages: [] }, { fetch: gemini }),
    (e) => e.kind === "key");
  const broke = fakeFetch(() => json(400, { error: { message: "Your credit balance is too low" } }));
  await assert.rejects(Providers.chat(ANTHROPIC, { system: "S", messages: [] }, { fetch: broke }), (e) => e.kind === "quota");
  const busy = fakeFetch(() => json(529, { error: { type: "overloaded_error" } }));
  await assert.rejects(Providers.chat(ANTHROPIC, { system: "S", messages: [] }, { fetch: busy }), (e) => e.kind === "server");
  const offline = async () => { throw new TypeError("Failed to fetch"); };
  await assert.rejects(Providers.chat(ANTHROPIC, { system: "S", messages: [] }, { fetch: offline }),
    (e) => e.kind === "network" && /couldn't reach Anthropic/.test(e.message));
  const noTools = fakeFetch(() => json(400, { error: { message: "This model does not support tools" } }));
  await assert.rejects(Providers.chat({ provider: "openai", key: "k", model: "m" },
    { system: "S", messages: [], tools: [{ name: "a", description: "", parameters: {} }] }, { fetch: noTools }), (e) => e.kind === "tools");
});

// ---------------------------------------------------------------- the store (keys)

test("a key is kept for this tab only, unless 'remember' is ticked", () => {
  const local = memoryStorage();
  const session = memoryStorage();
  const store = Store.create({ prefix: "t", local, session });
  assert.equal(store.ready(), false);
  store.saveSettings({ provider: "anthropic", key: "secret-1", model: "m" });
  assert.equal(store.ready(), true);
  assert.ok(session.data.get("t-settings").includes("secret-1"));
  assert.equal(local.data.has("t-settings"), false, "not on the device unless asked");
  store.saveAll({ ...store.all(), remember: true });
  assert.ok(local.data.get("t-settings").includes("secret-1"));
  assert.equal(session.data.has("t-settings"), false);
  assert.equal(Store.create({ prefix: "t", local, session: memoryStorage() }).settings().key, "secret-1", "remembered on this device");
});

test("'Remove my keys' deletes every key from both places", () => {
  const local = memoryStorage();
  const session = memoryStorage();
  const store = Store.create({ prefix: "t", local, session });
  store.saveSettings({ provider: "openai", key: "k1", model: "m", remember: true });
  store.setMedia("image", { provider: "openai", key: "k2", model: "img" });
  session.setItem("t-settings", "{\"keys\":[{\"provider\":\"gemini\",\"key\":\"k3\",\"model\":\"x\"}]}");
  store.forget();
  assert.equal(local.data.has("t-settings"), false);
  assert.equal(session.data.has("t-settings"), false);
  assert.equal(store.ready(), false);
  assert.equal(store.media("image"), null);
});

test("several chat keys: add, switch, remove", () => {
  const store = Store.create({ prefix: "t", local: memoryStorage(), session: memoryStorage() });
  const a = store.addKey({ provider: "anthropic", key: "a", model: "m1" }).key;
  const b = store.addKey({ provider: "gemini", key: "b", model: "m2" }).key;
  assert.equal(store.settings().provider, "gemini", "a new key is put in use");
  assert.equal(store.useKey(a.id), true);
  assert.equal(store.settings().key, "a");
  store.removeKey(a.id);
  assert.equal(store.settings().id, b.id);
  assert.equal(store.all().keys.length, 1);
});

test("image and video keys are separate, and Anthropic isn't offered for them", () => {
  const store = Store.create({ prefix: "t", local: memoryStorage(), session: memoryStorage() });
  store.setMedia("image", { provider: "anthropic", key: "x", model: "m" });
  assert.equal(store.media("image"), null);
  store.setMedia("video", { provider: "gemini", key: "v", model: "vid" });
  assert.equal(store.media("video").key, "v");
  assert.equal(store.ready(), false, "a video key is not a chat key");
});

test("memory is off by default; turning it off forgets it", () => {
  const local = memoryStorage();
  const store = Store.create({ prefix: "t", local, session: memoryStorage() });
  assert.deepEqual(store.memory(), { on: false, notes: [] });
  assert.ok(store.remember("short answers").error);
  store.setMemoryOn(true);
  assert.deepEqual(store.remember("I prefer short answers").notes, ["I prefer short answers"]);
  store.remember("Call me by my nickname");
  assert.deepEqual(store.forgetNote(0), ["Call me by my nickname"]);
  store.setMemoryOn(false);
  assert.equal(local.data.has("t-memory"), false);
});

test("the chat follows from page to page, and never starts half way through a tool call", () => {
  const session = memoryStorage();
  const store = Store.create({ prefix: "t", local: memoryStorage(), session });
  const long = [];
  for (let i = 0; i < 70; i += 1) {
    long.push({ role: "user", text: `q${i}` }, { role: "assistant", text: "", toolCalls: [{ id: `c${i}`, name: "x", args: {} }] },
      { role: "tool", results: [{ id: `c${i}`, content: "r" }] }, { role: "assistant", text: `a${i}` });
  }
  store.saveChat(long);
  const again = Store.create({ prefix: "t", local: memoryStorage(), session }).loadChat();
  assert.ok(again.length <= Store.LIMITS.messages);
  assert.equal(again[0].role, "user");
  assert.equal(again[again.length - 1].text, "a69");
});

test("blocked storage: Pilot still works for this page", () => {
  const store = Store.create({ prefix: "t", local: blocked, session: blocked });
  store.saveSettings({ provider: "anthropic", key: "k", model: "m" });
  assert.equal(store.ready(), true);
  assert.equal(store.canKeep, false);
});

// ---------------------------------------------------------------- the engine

function engineWith({ replies, tools, kid = false, confirmAnswers = [], planAnswer = "all", storeSettings = ANTHROPIC }) {
  const store = Store.create({ prefix: "e", local: memoryStorage(), session: memoryStorage() });
  store.saveSettings(storeSettings);
  const sent = [];
  const asked = { confirm: [], plan: [] };
  const providers = {
    ...Providers,
    chat: async (s, req) => { sent.push(req); const r = replies.shift(); if (r instanceof Error) throw r; return { raw: {}, toolCalls: [], stop: "done", ...r }; },
  };
  const host = {
    confirm: async (label, step) => { asked.confirm.push({ label, step }); return confirmAnswers.length ? confirmAnswers.shift() : false; },
    plan: async (labels) => { asked.plan.push(labels); return planAnswer; },
  };
  const engine = Engine.create({ store, providers, host,
    config: { appName: "Test App", knowledge: "The guide.", strengths: ["Honest."], tools: () => tools, kidView: () => kid,
      screen: () => ({ name: "Home", facts: "A fact." }) } });
  return { engine, store, sent, asked };
}

function trackedTools() {
  const ran = [];
  return {
    ran,
    tools: [
      { name: "show_list", kind: "read", description: "d", parameters: { type: "object", properties: {} }, run: () => { ran.push("show_list"); return { items: [] }; } },
      { name: "add_thing", kind: "change", description: "d", parameters: { type: "object", properties: { what: { type: "string", maxLength: 20 } }, required: ["what"] },
        describe: (a) => `add “${a.what}”`, run: (a) => { ran.push(`add:${a.what}`); return { ok: true }; } },
    ],
  };
}

test("a read-only tool runs straight away; a change waits for Allow", async () => {
  const t = trackedTools();
  const { engine, asked, sent } = engineWith({
    tools: t.tools, confirmAnswers: [false],
    replies: [{ toolCalls: [{ id: "1", name: "show_list", args: {} }, { id: "2", name: "add_thing", args: { what: "Feed the dog" } }] }, { text: "OK" }],
  });
  await engine.send("Do it");
  assert.deepEqual(t.ran, ["show_list"], "the change didn't run: the user pressed Cancel");
  assert.deepEqual(asked.confirm.map((c) => c.label), ["Add “Feed the dog”"]);
  const results = sent[1].messages.find((m) => m.role === "tool").results;
  assert.match(results.find((r) => r.id === "2").content, /said no/);
  assert.ok(sent[0].system.includes("The guide.") && sent[0].system.includes("Honest.") && sent[0].system.includes("Screen: Home"));
});

test("pressing Allow runs the change through the app's own function", async () => {
  const t = trackedTools();
  const { engine } = engineWith({ tools: t.tools, confirmAnswers: [true],
    replies: [{ toolCalls: [{ id: "2", name: "add_thing", args: { what: "Feed the dog" } }] }, { text: "Done" }] });
  await engine.send("Add it");
  assert.deepEqual(t.ran, ["add:Feed the dog"]);
  assert.equal(engine.messages[engine.messages.length - 1].text, "Done");
});

test("a plan is shown first: Approve all, Step through, or Cancel", async () => {
  for (const [answer, confirms, expected] of [["all", [], ["add:a", "add:b"]], ["step", [true, false], ["add:a"]], ["cancel", [], []]]) {
    const t = trackedTools();
    const { engine, asked } = engineWith({ tools: t.tools, planAnswer: answer, confirmAnswers: confirms.slice(),
      replies: [{ toolCalls: [{ id: "p", name: "pilot_plan", args: { steps: [{ tool: "add_thing", args: { what: "a" } }, { tool: "add_thing", args: { what: "b" } }] } }] }, { text: "ok" }] });
    await engine.send("Two things");
    assert.deepEqual(asked.plan, [["Add “a”", "Add “b”"]]);
    assert.deepEqual(t.ran, expected, answer);
    assert.equal(asked.confirm.length, answer === "step" ? 2 : 0);
  }
});

test("several changes asked for at once become one plan", async () => {
  const t = trackedTools();
  const { engine, asked } = engineWith({ tools: t.tools, planAnswer: "all",
    replies: [{ toolCalls: [{ id: "1", name: "add_thing", args: { what: "a" } }, { id: "2", name: "add_thing", args: { what: "b" } }] }, { text: "ok" }] });
  await engine.send("Two things");
  assert.equal(asked.plan.length, 1);
  assert.deepEqual(t.ran, ["add:a", "add:b"]);
});

test("a plan can't sneak in a read tool or one that doesn't exist", async () => {
  const t = trackedTools();
  const { engine, asked } = engineWith({ tools: t.tools,
    replies: [{ toolCalls: [{ id: "p", name: "pilot_plan", args: { steps: [{ tool: "delete_everything", args: {} }] } }] }, { text: "ok" }] });
  await engine.send("x");
  assert.equal(asked.plan.length, 0);
  assert.deepEqual(t.ran, []);
});

test("no tools at all on a child's view", async () => {
  const t = trackedTools();
  const { engine, sent } = engineWith({ tools: t.tools, kid: true, replies: [{ text: "hi" }] });
  assert.deepEqual(engine.tools(), []);
  await engine.send("hello");
  assert.deepEqual(sent[0].tools, []);
});

test("tools that pay, delete or message people are never offered", () => {
  for (const name of ["delete_project", "make_payment", "checkout_now", "send_message", "invite_friend", "change_plan_upgrade", "email_parent"]) {
    assert.equal(Engine.checkTool({ name, kind: "change", describe: () => "", run: () => {} }), null, name);
  }
  assert.ok(Engine.checkTool({ name: "add_step", kind: "change", describe: () => "", run: () => {} }));
  assert.equal(Engine.checkTool({ name: "add_step", kind: "change", run: () => {} }), null, "a change must say what it does");
});

test("a model that can't use tools: Pilot remembers and just chats", async () => {
  const t = trackedTools();
  const { engine, store, sent } = engineWith({ tools: t.tools,
    replies: [new Providers.PilotError("tools", "no tools"), { text: "Here's how to do it yourself." }] });
  await engine.send("Add a thing");
  assert.equal(store.settings().noTools, true);
  assert.deepEqual(sent[1].tools, []);
  assert.match(sent[1].system, /no tools right now/);
  assert.equal(engine.messages[engine.messages.length - 1].text, "Here's how to do it yourself.");
});

test("no key: Pilot says so instead of calling anyone", async () => {
  const { engine, store, sent } = engineWith({ tools: [], replies: [] });
  store.forget();
  assert.equal(await engine.send("hi"), false);
  assert.equal(sent.length, 0);
  assert.match(engine.messages[engine.messages.length - 1].text, /needs your own AI key/);
});

test("image and video tools appear only with an image or video key", () => {
  const { engine, store } = engineWith({ tools: [], replies: [] });
  const host = { image: () => "ok", video: () => "ok" };
  const withHost = Engine.create({ store, providers: Providers, host, config: { tools: [] } });
  assert.deepEqual(withHost.tools().map((t) => t.name), []);
  store.setMedia("image", { provider: "openai", key: "i", model: "img" });
  assert.deepEqual(withHost.tools().map((t) => t.name), ["make_image"]);
  store.setMedia("video", { provider: "openai", key: "v", model: "vid" });
  const video = withHost.tools().find((t) => t.name === "make_video");
  assert.equal(video.kind, "change", "a video costs more, so it asks first");
  assert.equal(engine.tools().length, 0, "a host that can't show pictures gets no picture tools");
});

// ---------------------------------------------------------------- the renderer

// Just enough of a document to build nodes, to prove nothing becomes HTML.
function fakeDocument() {
  const make = (tag) => ({ tagName: tag, children: [], attrs: {}, text: "",
    appendChild(c) { this.children.push(c); return c; }, setAttribute(k, v) { this.attrs[k] = v; },
    set textContent(v) { this.children = [{ tagName: "#text", text: v }]; }, set className(v) { this.attrs.class = v; } });
  return { createElement: make, createDocumentFragment: () => make("#fragment"), createTextNode: (t) => ({ tagName: "#text", text: t }) };
}
function walk(node, out = []) { out.push(node); for (const c of node.children || []) walk(c, out); return out; }

test("an answer that tries to inject HTML shows as plain text", () => {
  const evil = "Hi <script>alert(1)</script> <img src=x onerror=alert(2)> [click](javascript:alert(3)) **bold**";
  const nodes = walk(Render.render(evil, fakeDocument()));
  const tags = nodes.map((n) => n.tagName);
  assert.ok(!tags.includes("script") && !tags.includes("img") && !tags.includes("a"), tags.join());
  const text = nodes.filter((n) => n.tagName === "#text").map((n) => n.text).join("");
  assert.ok(text.includes("<script>alert(1)</script>") && text.includes("<img src=x onerror=alert(2)>"));
  assert.ok(tags.includes("strong"));
});

test("markdown: lists, code, and safe links only", () => {
  const blocks = Render.parse("# Title\n\n- one\n- **two**\n\n1. first\n2. second\n\n```\n<b>code</b>\n```\n\nSee [docs](https://example.com/a).");
  assert.deepEqual(blocks.map((b) => b.t), ["h", "ul", "ol", "pre", "p"]);
  assert.equal(blocks[3].v, "<b>code</b>");
  const nodes = walk(Render.render("[docs](https://example.com/a)", fakeDocument()));
  const a = nodes.find((n) => n.tagName === "a");
  assert.equal(a.attrs.href, "https://example.com/a");
  assert.equal(a.attrs.rel, "noopener noreferrer nofollow");
  assert.equal(Render.safeHref("javascript:alert(1)"), null);
  assert.equal(Render.plain("- **a**\n- b"), "- a\n- b");
});

// ---------------------------------------------------------------- pictures and videos

test("a picture with an OpenAI image key comes back as data, never a stored link", async () => {
  const fetcher = fakeFetch(() => json(200, { data: [{ b64_json: "aGVsbG8=" }] }));
  const pic = await Media.image({ provider: "openai", key: "i", model: "img" }, "a logo idea", { fetch: fetcher });
  assert.deepEqual(pic, { mime: "image/png", data: "aGVsbG8=" });
  assert.equal(fetcher.calls[0].url, "https://api.openai.com/v1/images/generations");
  assert.equal(fetcher.calls[0].body.prompt, "a logo idea");
});

test("a picture with a Gemini image key, both ways the provider makes them", async () => {
  const predict = fakeFetch(() => json(200, { predictions: [{ bytesBase64Encoded: "AAA=", mimeType: "image/png" }] }));
  assert.equal((await Media.image({ provider: "gemini", key: "g", model: "pic", method: "predict" }, "a cat", { fetch: predict })).data, "AAA=");
  assert.match(predict.calls[0].url, /:predict$/);
  const generate = fakeFetch(() => json(200, { candidates: [{ content: { parts: [{ text: "here" }, { inlineData: { mimeType: "image/jpeg", data: "BBB=" } }] } }] }));
  assert.deepEqual(await Media.image({ provider: "gemini", key: "g", model: "pic" }, "a cat", { fetch: generate }), { mime: "image/jpeg", data: "BBB=" });
  assert.deepEqual(generate.calls[0].body.generationConfig.responseModalities, ["TEXT", "IMAGE"]);
});

test("a video job: started, checked, and fetched with the key", async () => {
  const p = { provider: "openai", key: "v", model: "vid" };
  const fetcher = fakeFetch((url) => {
    if (url.endsWith("/videos")) return json(200, { id: "video_1", status: "queued" });
    if (url.endsWith("/videos/video_1")) return json(200, { id: "video_1", status: "in_progress", progress: 40 });
    return new Response(new Blob(["mp4"]), { status: 200 });
  });
  assert.deepEqual(await Media.startVideo(p, "a sunrise", { fetch: fetcher }), { id: "video_1" });
  assert.deepEqual(await Media.checkVideo(p, "video_1", { fetch: fetcher }), { state: "working", progress: 40 });
  const blob = await Media.fetchVideo(p, "video_1", { state: "done" }, { fetch: fetcher });
  assert.equal(await blob.text(), "mp4");
  await assert.rejects(Media.checkVideo(p, "../../etc", { fetch: fetcher }));
});

// ---------------------------------------------------------------- the mascot and the window

test("the robot is the owner's picture at the size the screen needs; the P mark is SVG with attributes only", () => {
  const made = [];
  const node = (tag) => { const n = { tag, attrs: {}, kids: [], setAttribute(k, v) { this.attrs[k] = v; },
    appendChild(c) { this.kids.push(c); }, set className(v) { this.attrs.class = v; } }; made.push(n); return n; };
  const doc = { createElementNS: (ns, tag) => node(tag), createElement: node };
  const img = Mascot.robot(doc, { small: "/r128.webp", large: "/r256.webp" });
  assert.equal(img.tag, "img");
  assert.equal(img.attrs.srcset, "/r128.webp 128w, /r256.webp 256w");
  assert.equal(img.attrs.alt, "");
  assert.ok(img.attrs.width && img.attrs.height, "its size is known before it arrives");
  const svg = Mascot.mark(doc);
  assert.equal(svg.tag, "svg");
  assert.ok(made.every((n) => !("style" in n.attrs)));
});

// The MTRX site's own pack and Ask Genesis tests stay in that repo. This
// app's pack and console tools are tested in tests/js/cyberlogix-pilot.test.js.

test("the window never writes the word null: every replaceChildren goes through put()", () => {
  const src = require("node:fs").readFileSync(require("node:path").join(__dirname, "../../static/pilot/pilot-panel.js"), "utf8");
  const calls = src.match(/\.replaceChildren\(/g) || [];
  assert.equal(calls.length, 1, "only put() may call replaceChildren directly");
  assert.match(src, /function put\(node, \.\.\.kids\) \{\n\s+node\.replaceChildren\(\.\.\.kids\.filter\(/);
});

// ---------------------------------------------------------------- the moving robot (core 1.1.0)

const Motion = require(core("pilot-motion.js"));
const webGeo = (extra) => ({ pos: { x: 0, y: 0 }, home: { x: 0, y: 0 }, floor: 0, left: -1100, right: 40, top: -560, u: 88,
  profile: "web", sky: 240, poses: ["standing", "wave", "walk1", "run1", "run2", "fly", "climb1", "flip"], ...(extra || {}) });
const seeded = (seed) => { let s = seed; return () => ((s = (s * 16807) % 2147483647) / 2147483647); };

test("motion: a pose without a picture falls back, in the end to the resting robot", () => {
  assert.equal(Motion.resolvePose("walk2", ["standing", "walk1"]), "standing");
  assert.equal(Motion.resolvePose("climb2", ["climb1"]), "climb1");
  assert.equal(Motion.resolvePose("throw", ["wave"]), "wave");
  assert.equal(Motion.resolvePose("kick", ["run2"]), "run2");
  assert.equal(Motion.resolvePose("tennis", ["standing"]), "standing");
  assert.equal(Motion.resolvePose("fly", []), "rest");
  assert.deepEqual(Motion.posesFromFiles(["wave.png", "wave.webp", "kick.png", "readme.txt", "dance.webp"]), { wave: "wave.webp", kick: "kick.png" });
  assert.deepEqual(Motion.posesFromFiles(["arms-crossed.webp", "land.png"]), { "arms-crossed": "arms-crossed.webp", land: "land.png" });
  for (const name of ["throw", "tennis", "kick", "celebrate", "slide", "point", "power", "land", "love", "arms-crossed"]) assert.ok(Motion.POSES.includes(name), name);
});

test("motion: the web robot keeps still under reduce motion, whatever is chosen", () => {
  const r = seeded(9);
  for (const energy of ["low", "normal", "playful"]) {
    for (const calm of [null, false]) {
      for (let i = 0; i < 100; i += 1) assert.equal(Motion.pick({ profile: "web", reducedMotion: true, energy, calm }, r), null);
    }
  }
  assert.equal(Motion.stillReason({ profile: "web", reducedMotion: true, calm: false }), "reduced-motion");
  assert.equal(Motion.pick({ profile: "web", chatOpen: true }, r), null);
  // The actor isn't even started: live() returns null under reduce motion.
  const saved = globalThis.matchMedia;
  globalThis.matchMedia = () => ({ matches: true });
  const Actor = require(core("pilot-actor.js"));
  assert.equal(Actor.live({ launcher: null }), null);
  globalThis.matchMedia = saved;
});

test("motion: on the web, no climbing, flights, soccer or tennis unless Playful, and every move ends at home", () => {
  const seen = (energy) => { const r = seeded(21); const s = new Set(); for (let i = 0; i < 3000; i += 1) s.add(Motion.pick({ profile: "web", energy }, r)); return s; };
  const normal = seen("normal");
  assert.ok(!normal.has("climb") && !normal.has("fly") && !normal.has("run"), [...normal].join());
  // The calmer new moves by default; the rest only on Playful.
  assert.ok(normal.has("celebrate") && normal.has("love") && normal.has("point"), [...normal].join());
  assert.ok(!normal.has("slide") && !normal.has("power"), [...normal].join());
  const playful = seen("playful");
  assert.ok(playful.has("climb") && playful.has("fly") && playful.has("sport") && playful.has("slide") && playful.has("power"));
  for (let i = 1; i <= 20; i += 1) assert.equal(Motion.plan("sport", webGeo({ energy: "normal", random: seeded(i) })).name, "football");
  for (const name of ["stroll", "peek", "wave", "backflip", "spin", "bounce", "football", "soccer", "tennis", "fly", "climb"]) {
    const p = Motion.plan(name, webGeo({ energy: "playful", random: seeded(3) }));
    assert.ok(p, name);
    assert.ok(Math.abs(p.end.x) < 2 && Math.abs(p.end.y) < 2, `${name} ends at ${JSON.stringify(p.end)}`);
    // Props stay in the page's bottom area.
    for (const pr of p.props) for (const f of pr.frames) if (pr.on === "stage") assert.ok(f.y >= -240 - 88, `${name} ${pr.kind} y ${f.y}`);
  }
});

// ---------------------------------------------------------------- staying out of the way (core 1.1.0)

function fakeEl(box, extra) {
  const e = { box, hiddenAttr: false, inside: null, ...(extra || {}) };
  e.getClientRects = () => (box ? [box] : []);
  e.getBoundingClientRect = () => box || { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
  e.closest = (sel) => (sel === "[hidden]" && e.hiddenAttr ? e : null);
  return e;
}
const fakeDoc = (map) => ({ querySelectorAll: (sel) => map[sel] || [] });
const box = (left, top, w, h) => ({ left, top, right: left + w, bottom: top + h, width: w, height: h });
const win = { getComputedStyle: (el) => el.style || { visibility: "visible", display: "block", opacity: "1" } };

test("placement: a page's open pop-up makes the robot step back, but not Pilot's own window or a closed one", () => {
  const own = { contains: (el) => el.own === true };
  const open = fakeEl(box(100, 100, 400, 300));
  const closed = fakeEl(null);
  const hiddenOne = fakeEl(box(0, 0, 10, 10), { hiddenAttr: true });
  const invisible = fakeEl(box(0, 0, 10, 10), { style: { visibility: "hidden", display: "block", opacity: "1" } });
  const pilotsOwn = fakeEl(box(0, 0, 400, 600), { own: true });
  assert.equal(Mascot.modalOpen(fakeDoc({ [Mascot.MODALS]: [open] }), own, win), true);
  assert.equal(Mascot.modalOpen(fakeDoc({ [Mascot.MODALS]: [closed, hiddenOne, invisible] }), own, win), false);
  assert.equal(Mascot.modalOpen(fakeDoc({ [Mascot.MODALS]: [pilotsOwn] }), own, win), false);
  for (const sel of ["dialog[open]", '[aria-modal="true"]', '[role="dialog"]']) assert.ok(Mascot.MODALS.includes(sel), sel);
  const css = fs.readFileSync(core("pilot.css"), "utf8");
  assert.match(css, /\.pilot-root\.pilot-yield \{[^}]*visibility: hidden;[^}]*pointer-events: none;/);
});

test("placement: the robot keeps clear of things to avoid and of another helper, else tucks away", () => {
  const view = { width: 390, height: 800 };
  const size = { width: 56, height: 54 };
  // Nothing in the way: its own corner.
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, blocks: [] }), { corner: "br", peek: false });
  // Another helper (OmniArmor's Armo) in the bottom right: the robot moves to the bottom left.
  const armo = box(300, 690, 80, 100);
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, blocks: [armo] }), { corner: "bl", peek: false });
  // A full-width bar along the bottom: a top corner.
  const bar = box(0, 700, 390, 100);
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, blocks: [bar] }), { corner: "tr", peek: false });
  // Everything taken: it stays in its corner, tucked half away until tapped.
  const all = [bar, box(0, 0, 390, 100)];
  assert.deepEqual(Mascot.choosePlace({ corner: "bl", view, size, gap: 12, blocks: all }), { corner: "bl", peek: true });
  assert.deepEqual(Mascot.cornerOrder("tl"), ["tl", "tr", "bl", "br"]);
  // Only visible elements, outside Pilot, count.
  const doc = fakeDoc({ ".armo": [fakeEl(box(1, 2, 30, 40)), fakeEl(null)], ".bad[": undefined });
  doc.querySelectorAll = ((orig) => (sel) => { if (sel === ".bad[") throw new Error("bad selector"); return orig(sel); })(doc.querySelectorAll);
  assert.deepEqual(Mascot.boxesOf(doc, [".armo", ".bad["], null, win), [{ left: 1, top: 2, right: 31, bottom: 42 }]);
});

test("placement: smaller on phones, a gutter pages can reserve, and this app's banners kept clear", () => {
  const css = fs.readFileSync(core("pilot.css"), "utf8");
  assert.match(css, /@media \(max-width: 720px\) \{\s*\.pilot-root \{ --pilot-size: 56px;/);
  assert.match(css, /:root\.pilot-on \{ --pilot-gutter:/);
  assert.match(css, /\.pilot-gutter \{ padding-bottom: var\(--pilot-gutter/);
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  assert.ok(loader.includes("config.avoid") && loader.includes("config.yieldTo"));
  // This app's own list (the MTRX site checks its Genesis bars here).
  const cfg = fs.readFileSync(app("pilot-cyberlogix.js"), "utf8");
  assert.ok(cfg.includes('avoid: ["#site-wide-banner"'));
});

// ---------------------------------------------------------------- core 1.1.1: clear of the page wherever it scrolls

test("placement 1.1.1: words and buttons under a corner make it busy; a free corner is found, else it tucks where nothing shows", () => {
  const view = { width: 375, height: 800 };
  const size = { width: 56, height: 54 };
  // Words in the bottom right only (busy() is what contentIn answers): the bottom left.
  const wordsAt = (corners) => (b, c) => corners.includes(c);
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, busy: wordsAt(["br"]) }), { corner: "bl", peek: false });
  // Words everywhere: tucked away; the slice that still shows is asked about too.
  const everywhere = (b) => b.right - b.left > 30; // full boxes are busy, the thin slices are not
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, busy: everywhere, peekShow: 24 }), { corner: "br", peek: true });
  // The slice is exactly as wide as pilot.css shows it, at the screen's edge.
  assert.deepEqual(Mascot.peekBox("br", view, size, 12, 24), { left: 351, top: 734, right: 375, bottom: 788 });
  assert.deepEqual(Mascot.peekBox("tl", view, size, 12, 24), { left: 0, top: 12, right: 24, bottom: 66 });
});

test("placement 1.1.1: a box being typed in is never covered, not even by the tucked-away robot", () => {
  const view = { width: 375, height: 800 };
  const size = { width: 56, height: 54 };
  // The field runs across the bottom of the screen: a top corner.
  const field = box(16, 730, 343, 56);
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, hard: [field] }), { corner: "tr", peek: false });
  // Words in every corner too: tucked away, but in a corner clear of the field.
  const busy = () => true;
  const place = Mascot.choosePlace({ corner: "br", view, size, gap: 12, hard: [field], busy, peekShow: 24 });
  assert.equal(place.peek, true);
  assert.ok(place.corner[0] === "t", place.corner);
  // Only typing boxes count.
  const input = (type, extra = {}) => ({ nodeType: 1, tagName: "INPUT", type, getAttribute: () => type, ...extra });
  assert.equal(Mascot.isEditable(input("text")), true);
  assert.equal(Mascot.isEditable(input("email")), true);
  assert.equal(Mascot.isEditable(input("checkbox")), false);
  assert.equal(Mascot.isEditable(input("submit")), false);
  assert.equal(Mascot.isEditable(input("text", { disabled: true })), false);
  assert.equal(Mascot.isEditable({ nodeType: 1, tagName: "TEXTAREA" }), true);
  assert.equal(Mascot.isEditable({ nodeType: 1, tagName: "DIV", isContentEditable: true }), true);
  assert.equal(Mascot.isEditable({ nodeType: 1, tagName: "DIV" }), false);
  assert.equal(Mascot.isEditable(null), false);
});

test("placement 1.1.1: contentIn finds words and buttons under the robot's box, never Pilot's own, never scenery", () => {
  const area = { left: 300, top: 700, right: 360, bottom: 760 };
  const rect = (l, t, w, h) => ({ left: l, top: t, right: l + w, bottom: t + h, width: w, height: h });
  // A paragraph whose second line ends under the robot, a button in the corner, a big picture and Pilot itself.
  const text = (rects) => ({ nodeType: 3, nodeValue: "Some words", rects });
  const para = { nodeType: 1, tagName: "P", closest: () => null, childNodes: [text([rect(16, 640, 330, 20), rect(16, 712, 300, 20)])],
    getBoundingClientRect: () => rect(16, 640, 330, 100) };
  const shortPara = { nodeType: 1, tagName: "P", closest: () => null, childNodes: [text([rect(16, 712, 120, 20)])],
    getBoundingClientRect: () => rect(16, 700, 340, 60) };
  const button = { nodeType: 1, tagName: "BUTTON", getBoundingClientRect: () => rect(320, 720, 40, 30) };
  button.closest = () => button;
  const span = { nodeType: 1, tagName: "SPAN", closest: () => button, childNodes: [] };
  const picture = { nodeType: 1, tagName: "IMG", closest: () => null, childNodes: [], getBoundingClientRect: () => rect(0, 500, 375, 300) };
  const icon = { nodeType: 1, tagName: "IMG", closest: () => null, childNodes: [], getBoundingClientRect: () => rect(310, 710, 40, 40) };
  const pilot = { nodeType: 1, tagName: "IMG", own: true };
  const own = { contains: (el) => el && el.own === true };
  const docWith = (stack) => ({
    elementsFromPoint: () => stack,
    createRange: () => { let node = null; return { selectNodeContents: (n) => { node = n; }, getClientRects: () => node.rects }; },
  });
  assert.equal(Mascot.contentIn(docWith([pilot, para]), area, own).length, 1, "a line ending under the robot");
  assert.equal(Mascot.contentIn(docWith([pilot, shortPara]), area, own).length, 0, "a short line that ends before it");
  assert.equal(Mascot.contentIn(docWith([pilot, span]), area, own).length, 1, "a button (through its label)");
  assert.equal(Mascot.contentIn(docWith([pilot, picture]), area, own).length, 0, "a big picture is scenery");
  assert.equal(Mascot.contentIn(docWith([icon]), area, own).length, 1, "an icon counts");
  assert.equal(Mascot.contentIn(docWith([pilot]), area, own).length, 0, "Pilot's own robot never counts");
  assert.deepEqual(Mascot.contentIn({}, area, own), [], "a browser without elementsFromPoint: nothing found, nothing breaks");
  // It looks at a small grid of points, edge to edge (16 by default), not the whole page.
  let calls = 0;
  Mascot.contentIn({ elementsFromPoint: () => { calls += 1; return []; } }, area, own);
  assert.equal(calls, 16);
});

test("placement 1.1.1: the loader looks again on scroll and resize (once a frame), on focus, and keeps line ends clear on phones", () => {
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  assert.match(loader, /requestAnimationFrame\(fit\)/);
  assert.match(loader, /addEventListener\("scroll", \(\) => \{[\s\S]*?queueFit\(false\);/);
  assert.match(loader, /addEventListener\("resize"/);
  assert.match(loader, /addEventListener\("focusin", \(\) => queueFit\(true\)\)/);
  assert.match(loader, /addEventListener\("focusout"/);
  assert.match(loader, /M\(\)\.placeNow\(\{/);
  // While typing, a tap never brings it back out onto the box.
  assert.match(loader, /place\.peek && \(Date\.now\(\) > peekOff \|\| place\.typing\)/);
  // The side gutter: the app's elements (main by default).
  assert.match(loader, /list\(config\.gutter\)\.length \? list\(config\.gutter\) : \["main"\]/);
  const mascot = fs.readFileSync(core("pilot-mascot.js"), "utf8");
  // (1.2.0: with a little room around the robot, and the small tucked-away slice as it is.)
  assert.match(mascot, /contentIn\(o\.doc, small \? box : grow\(box, cx, cy\), o\.own\)\.length > 0/);
  assert.match(mascot, /hard: field \? \[field\.getBoundingClientRect\(\)\] : \[\]/);
  // Mid-scroll it only checks its own spot.
  assert.match(mascot, /const spot = cornerBox\(o\.shown, view, size, gap\);/);
  assert.match(mascot, /paddingRight\) \|\| 0\) < want/);
  const css = fs.readFileSync(core("pilot.css"), "utf8");
  assert.match(css, /--pilot-peek-show: 20px;/);
  assert.match(css, /:root\.pilot-on \.pilot-gutter-side \{\s*padding-right: max\(var\(--pilot-side\)[^}]*padding-left: max\(var\(--pilot-side\)/);
  assert.match(css, /--pilot-tuck: calc\(var\(--pilot-size\) \+ var\(--pilot-gap\) - var\(--pilot-peek-show\)\)/);
  // 24px of room is more than the 20px slice that shows.
  assert.match(css, /--pilot-side: 24px;/);
});

test("the chat window 1.1.1: suggestions come first once chatting; the grown-ups note sits above the message box", () => {
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  assert.ok(panel.includes('el("div", { class: "pilot-view pilot-chat-view" }, welcome, chips, log, cardBox)'));
  assert.ok(panel.includes('el("footer", { class: "pilot-foot" }, footNote, form, bottom)'));
  assert.ok(panel.includes("welcome.hidden = state.messages.length > 0;"));
  assert.ok(panel.includes('const want = Boolean(config.keyNote) && !store.ready() && view === "chat";'));
  // The five pills are still the welcome.
  for (const pill of ["Ask anything…", "Get things done…", "Help with code…", "Analyze & explain…", "Customize with your API key"]) {
    assert.ok(panel.includes(pill), pill);
  }
  // This app's own note says what an AI key is and where to get one (the
  // MTRX site checks its own note and its Genesis rows here).
  const cfg = fs.readFileSync(app("pilot-cyberlogix.js"), "utf8");
  assert.ok(cfg.includes(`keyNote: "An AI key is a private code you make on an AI company's website`));
  assert.ok(cfg.includes("AI keys are for grown-ups (the AI companies require 18+)."));
  // On CyberLogix the console's tabs, the Needs you now bar and the front
  // page's first-screen facts are kept clear too.
  for (const sel of [".finder", ".f-tab", "#needs-bar", ".needs-bar-acts", "#facts"]) assert.ok(cfg.includes(`"${sel}"`), sel);
});

test("placement 1.1.1: placeNow keeps off a focused box, and mid-scroll only tucks in place", () => {
  const rect = (l, t, w, h) => ({ left: l, top: t, right: l + w, bottom: t + h, width: w, height: h });
  const field = { nodeType: 1, tagName: "TEXTAREA", getBoundingClientRect: () => rect(16, 700, 343, 90) };
  const body = {};
  const own = { contains: () => false };
  const win = { innerWidth: 375, innerHeight: 800,
    getComputedStyle: () => ({ getPropertyValue: (n) => ({ "--pilot-gap": "12px", "--pilot-peek-show": "24px" })[n] || "" }) };
  const launcher = { getBoundingClientRect: () => rect(307, 734, 56, 54) };
  const doc = { body, activeElement: field, querySelectorAll: () => [], elementsFromPoint: () => [] };
  const typing = Mascot.placeNow({ doc, win, own, launcher, avoid: [], corner: "br", shown: "br", full: false, text: true });
  assert.deepEqual(typing, { corner: "tr", peek: false, typing: true });
  // Not typing, mid-scroll, words under its spot: it tucks away where it is.
  doc.activeElement = body;
  const words = { nodeType: 1, tagName: "BUTTON", getBoundingClientRect: () => rect(0, 600, 375, 200) };
  words.closest = () => words;
  doc.elementsFromPoint = () => [words];
  const mid = Mascot.placeNow({ doc, win, own, launcher, avoid: [], corner: "br", shown: "bl", full: false, text: true });
  assert.deepEqual(mid, { corner: "bl", peek: true, typing: false });
  // Text check off: free.
  assert.deepEqual(Mascot.placeNow({ doc, win, own, launcher, avoid: [], corner: "br", shown: "br", full: true, text: false }),
    { corner: "br", peek: false, typing: false });
});

// ---------------------------------------------------------------- core 1.2.0: each app's own welcome, and noticed but never in the way

const vm = require("node:vm");
// The chat window's file, run with a bare window: its pure helpers only.
function panelHelpers() {
  const sandbox = { window: {}, document: {} };
  vm.runInNewContext(fs.readFileSync(core("pilot-panel.js"), "utf8"), sandbox);
  const P = sandbox.window.PilotPanel;
  // Plain values from this realm, so deepEqual compares them fairly.
  const plain = (v) => JSON.parse(JSON.stringify(v));
  return { ...P, welcomePills: (g) => plain(P.welcomePills(g)), hiddenSections: (g) => plain([...P.hiddenSections(g)]) };
}

test("welcome 1.2.0: the design's five pills and tagline unless the app gives its own", () => {
  const P = panelHelpers();
  assert.equal(P.VERSION, "1.2.1");
  assert.equal(P.DEFAULT_TAGLINE, "Your AI copilot. Powered by you.");
  const five = P.welcomePills(undefined);
  assert.deepEqual(five.map((p) => p.label), ["Ask anything…", "Get things done…", "Help with code…", "Analyze & explain…", "Customize with your API key"]);
  assert.deepEqual(five.map((p) => p.action), ["ask", "task", "code", "explain-screen", "settings"]);
  assert.ok(five.every((p) => p.builtin));
  // Nothing usable given: still the design's five.
  assert.equal(P.welcomePills([null, { label: "", action: "x" }, { label: "No action" }, "words"]).length, 5);
});

test("welcome 1.2.0: an app's pills are cleaned: words only, a known icon, built-ins or a prompt, at most five", () => {
  const P = panelHelpers();
  const pills = P.welcomePills([
    { icon: "spark", label: "  Help with   my idea ", action: "Help me with my idea: " },
    { icon: "nope", label: "What is Orbyt?", action: "What is Orbyt?", send: true },
    { icon: "bars", label: "Explain this page", action: "explain-screen" },
    { icon: "key", label: "Add my AI key", action: "settings" },
    { icon: "chat", label: "x".repeat(80), action: "summarise-screen" },
    { icon: "chat", label: "Sixth", action: "help" },
  ]);
  assert.equal(pills.length, 5);
  assert.deepEqual(pills[0], { icon: "spark", label: "Help with my idea", action: "Help me with my idea: ", builtin: false, send: false });
  assert.deepEqual(pills[1], { icon: "spark", label: "What is Orbyt?", action: "What is Orbyt?", builtin: false, send: true });
  assert.equal(pills[2].builtin, true);
  assert.equal(pills[3].action, "settings");
  assert.equal(pills[4].label.length, 40);
  // Key sections an app can hide: only image and video.
  assert.deepEqual(P.hiddenSections(["image", "video", "chat", 3]), ["image", "video"]);
  assert.deepEqual(P.hiddenSections(undefined), []);
});

test("welcome 1.2.0: the window uses them, shows this page's suggestions on the welcome, and hides the key sections asked", () => {
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  assert.ok(panel.includes("const pills = welcomePills(config.welcomePills);"));
  assert.ok(panel.includes('el("div", { class: "pilot-pills" }, ...pills.map((p) => pill(p.icon, p.label, () => runPill(p))))'));
  assert.ok(panel.includes('(typeof config.tagline === "string" && config.tagline.trim()) || DEFAULT_TAGLINE'));
  // This page's suggestions are on the welcome too (before any chat, before a key).
  assert.match(panel, /here\.length \? el\("div", \{ class: "pilot-chips pilot-chips-welcome"/);
  assert.ok(panel.includes('hidden.has("image") ? null : mediaSection("image"'));
  assert.ok(panel.includes('hidden.has("video") ? null : mediaSection("video"'));
});

test("welcome 1.2.0: a message typed with no key stays in the box, key settings open, and it sends once a key is added", () => {
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  // Sending with no key: the box isn't emptied, and ask() holds the words.
  assert.match(panel, /if \(store\.ready\(\) \|\| !text\.trim\(\)\) \{\s*input\.value = "";/);
  assert.ok(panel.includes('if (!store.ready()) { if (!extra) hold({ text: words }); else show("settings"); return; }'));
  assert.ok(panel.includes('if (!store.ready()) { hold({ screen: [prompt, label] }); return; }'));
  assert.match(panel, /function hold\(job\) \{\s*pending = job;\s*if \(job\.text\) fill\(job\.text\);\s*show\("settings"\);/);
  // A key added (here or in another tab): the waiting message goes.
  assert.match(panel, /function changed\(\) \{[\s\S]*?pilot\.changed\(\);\s*sendPending\(\);/);
  assert.ok(panel.includes("if (pending && store.ready()) setTimeout(sendPending, 0);"));
  assert.ok(panel.includes("Your message is waiting in the box. It sends as soon as you add a key."));
});

test("placement 1.2.0: the first look of a visit shows the robot whole, once per visit, never on a box being typed in", () => {
  const tab = memoryStorage();
  assert.equal(Mascot.firstLook(tab, "mtrx-pilot-hello"), 4000);
  assert.equal(Mascot.firstLook(tab, "mtrx-pilot-hello"), 0); // the next page of the visit
  assert.equal(Mascot.firstLook(memoryStorage(), "k", 0), 0); // turned off
  assert.equal(Mascot.firstLook(memoryStorage(), "k", 60000), 10000);
  assert.equal(Mascot.firstLook(blocked, "k"), 4000); // storage blocked: shown this once
  const rect = (l, t, w, h) => ({ left: l, top: t, right: l + w, bottom: t + h, width: w, height: h });
  const words = { nodeType: 1, tagName: "BUTTON", getBoundingClientRect: () => rect(0, 0, 375, 800) };
  words.closest = () => words;
  const win = { innerWidth: 375, innerHeight: 800, getComputedStyle: () => ({ getPropertyValue: () => "" }) };
  const launcher = { offsetWidth: 56, offsetHeight: 54, getBoundingClientRect: () => rect(307, 734, 56, 54) };
  const doc = { body: {}, activeElement: null, querySelectorAll: () => [], elementsFromPoint: () => [words] };
  doc.activeElement = doc.body;
  const o = { doc, win, own: { contains: () => false }, launcher, avoid: [], corner: "br", shown: "br", full: true, text: true };
  assert.equal(Mascot.placeNow(o).peek, true);
  assert.equal(Mascot.placeNow({ ...o, reveal: true }).peek, false);
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  assert.ok(loader.includes('M().firstLook(storage("sessionStorage"), `${store.prefix}-hello`, config.revealMs)'));
  assert.ok(loader.includes("reveal: Date.now() < revealUntil"));
  const css = fs.readFileSync(core("pilot.css"), "utf8");
  assert.match(css, /\.pilot-revealing \.pilot-launcher::before \{ animation:/);
});

test("placement 1.2.1: the first look's class on the root is its own, never a class the chat window styles", () => {
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  const added = [...loader.matchAll(/rootEl\.classList\.add\("([\w-]+)"\)/g)].map((m) => m[1]);
  assert.ok(added.includes("pilot-revealing"));
  const panelCss = fs.readFileSync(core("pilot-panel.css"), "utf8");
  for (const name of added) assert.ok(!new RegExp(`\\.${name}(?![\\w-])`).test(panelCss), `${name} is styled by the chat window`);
});

test("placement 1.2.0: keepInViewport: tucked away it shrinks into its corner, never off the edge, and is measured at its own size", () => {
  const view = { width: 375, height: 800 };
  const size = { width: 56, height: 54 };
  const keep = { scale: 0.42, margin: 3 };
  for (const c of ["br", "bl", "tr", "tl"]) {
    const b = Mascot.peekBox(c, view, size, 12, 20, keep);
    assert.ok(b.left >= 3 && b.top >= 3 && b.right <= 372 && b.bottom <= 797, `${c} ${JSON.stringify(b)}`);
    assert.ok(Math.abs(b.right - b.left - 56 * 0.42) < 0.01);
  }
  // Without it, the old slice at the very edge.
  assert.deepEqual(Mascot.peekBox("br", view, size, 12, 20), { left: 355, top: 734, right: 375, bottom: 788 });
  // Everything busy: the small robot's box decides where it tucks.
  const busyLeft = (b) => b.left < 200;
  assert.deepEqual(Mascot.choosePlace({ corner: "bl", view, size, gap: 12, busy: (b, c, small) => !small || busyLeft(b), keep }), { corner: "br", peek: true });
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  assert.ok(loader.includes('if (config.keepInViewport) for (const n of [rootEl, document.documentElement]) n.classList.add("pilot-keep-in");'));
  assert.ok(loader.includes("keepIn: Boolean(config.keepInViewport)"));
  const mascot = fs.readFileSync(core("pilot-mascot.js"), "utf8");
  // Shrunk, its drawn box is smaller: placement measures its own size.
  assert.ok(mascot.includes("width: o.launcher.offsetWidth || r.width"));
  const css = fs.readFileSync(core("pilot.css"), "utf8");
  assert.match(css, /\.pilot-root\.pilot-keep-in\.pilot-peek \.pilot-launcher,[\s\S]*?scale\(var\(--pilot-peek-scale\)\)/);
  assert.match(css, /:root\.pilot-on\.pilot-keep-in \{ --pilot-side: 30px; \}/);
});

test("placement 1.2.0: pop-ups that aren't marked as dialogs: the app's selectors, and any fixed layer covering the screen", () => {
  const own = { contains: (el) => el.own === true };
  const sheet = fakeEl(box(0, 0, 375, 800));
  assert.equal(Mascot.modalOpen(fakeDoc({ ".sheet:target": [sheet] }), own, win, [".sheet:target"]), true);
  assert.equal(Mascot.modalOpen(fakeDoc({ ".sheet:target": [sheet] }), own, win), false);
  // A backdrop: fixed, covering the screen, on top at the middle and at the robot's corner.
  const W = { innerWidth: 375, innerHeight: 800, getComputedStyle: (el) => el.style };
  const layer = { nodeType: 1, tagName: "DIV", style: { position: "fixed", zIndex: "50" }, getBoundingClientRect: () => box(0, 0, 375, 800), parentElement: null };
  layer.contains = (el) => el === layer;
  const doc = { querySelectorAll: () => [], elementsFromPoint: () => [layer] };
  assert.equal(Mascot.covered(doc, own, W), true);
  assert.equal(Mascot.modalOpen(doc, own, W), true);
  // Page scenery behind everything (a negative z-index) never counts; nor a fixed bar.
  layer.style.zIndex = "-1";
  assert.equal(Mascot.covered(doc, own, W), false);
  const bar = { nodeType: 1, tagName: "HEADER", style: { position: "fixed", zIndex: "10" }, getBoundingClientRect: () => box(0, 0, 375, 60), parentElement: null };
  assert.equal(Mascot.covered({ querySelectorAll: () => [], elementsFromPoint: () => [bar] }, own, W), false);
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  assert.ok(loader.includes("M().modalOpen(document, rootEl, window, list(config.modals))"));
  assert.ok(loader.includes('window.addEventListener("hashchange", () => queueFit(true));'));
});

test("placement 1.2.0: a row of dots with tooltips counts, and line ends just beside the robot count too", () => {
  const rect = (l, t, w, h) => ({ left: l, top: t, right: l + w, bottom: t + h, width: w, height: h });
  // A streak dot: no words, not a button, but its day has a tooltip.
  const day = { nodeType: 1, tagName: "LI", getBoundingClientRect: () => rect(330, 760, 34, 40) };
  const dot = { nodeType: 1, tagName: "SPAN", childNodes: [], getBoundingClientRect: () => rect(336, 760, 22, 22),
    closest: (sel) => (sel === Mascot.MEANS ? day : null) };
  const doc = { createRange: () => ({ selectNodeContents() {}, getClientRects: () => [] }) };
  assert.deepEqual(Mascot.contentBox(dot, rect(320, 740, 56, 54), null, doc), { left: 330, top: 760, right: 364, bottom: 800 });
  assert.ok(Mascot.MEANS.includes("[title]") && Mascot.MEANS.includes("[data-pilot-clear]"));
  // A line ending 8px left of the robot's box: busy (its hand reaches there).
  const line = { nodeType: 1, tagName: "P", closest: () => null, getBoundingClientRect: () => rect(30, 740, 269, 20) };
  line.childNodes = [{ nodeType: 3, nodeValue: "words" }];
  const ldoc = { body: {}, querySelectorAll: () => [], elementsFromPoint: (x) => (x < 299 ? [line] : []),
    createRange: () => ({ selectNodeContents() {}, getClientRects: () => [rect(30, 740, 269, 20)] }) };
  ldoc.activeElement = ldoc.body;
  const win = { innerWidth: 375, innerHeight: 800, getComputedStyle: () => ({ getPropertyValue: () => "" }) };
  const launcher = { offsetWidth: 56, offsetHeight: 54, getBoundingClientRect: () => rect(307, 734, 56, 54) };
  const placed = Mascot.placeNow({ doc: ldoc, win, own: { contains: () => false }, launcher, avoid: [], corner: "br", shown: "br", full: false, text: true });
  assert.equal(placed.peek, true);
});

test("CyberLogix's own welcome: its tagline and five pills, no picture or video keys, kept on screen, its phone menu", () => {
  const cfg = fs.readFileSync(app("pilot-cyberlogix.js"), "utf8");
  assert.ok(cfg.includes('tagline: "Your fridge-alarm helper",'));
  for (const label of ["Which fridges need me?", "What does this alert mean?", "Is the food still OK?", "Add a fridge", "Add my AI key"]) {
    assert.ok(cfg.includes(`label: "${label}"`), label);
  }
  assert.ok(cfg.includes('hideKeySections: ["image", "video"],'));
  assert.ok(cfg.includes("keepInViewport: true,"));
  assert.ok(cfg.includes('modals: [".menu[open] > nav"],'));
  // The config's pills come through the core's cleaning as the five given,
  // on a public page and on the console alike.
  const sandbox = { window: {}, document: {} };
  vm.runInNewContext(fs.readFileSync(core("pilot-panel.js"), "utf8"), sandbox);
  for (const pathname of ["/", "/console", "/today"]) {
    let given = null;
    vm.runInNewContext(cfg, { window: { Pilot: { mount: (c) => { given = c; } }, location: { pathname }, localStorage: { getItem: () => null } },
      document: { getElementById: () => ({ dataset: {} }), title: "" } });
    const pills = JSON.parse(JSON.stringify(sandbox.window.PilotPanel.welcomePills(given.welcomePills)));
    assert.deepEqual(pills.map((p) => [p.label, p.builtin, p.send]), [["Which fridges need me?", false, true],
      ["What does this alert mean?", false, true], ["Is the food still OK?", false, true], ["Add a fridge", false, true],
      ["Add my AI key", true, false]], pathname);
    assert.equal(given.keepInViewport, true);
    assert.deepEqual([...sandbox.window.PilotPanel.hiddenSections(given.hideKeySections)], ["image", "video"]);
    // A public page tells the AI it sees nobody's fridges there.
    const sc = given.screen();
    if (pathname === "/") assert.match(sc.facts, /^Not signed in: Pilot can't see their fridges or alerts here\./);
  }
});
