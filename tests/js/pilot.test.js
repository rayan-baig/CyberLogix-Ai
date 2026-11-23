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
  assert.match(loader, /addEventListener\("scroll", \(e?\) => \{[\s\S]*?queueFit\(false\);/);
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
  // (1.4.2: links and buttons within CONTROL_PAD count too on a computer.)
  assert.match(mascot, /contentIn\(o\.doc, small \? box : grow\(box, cx, cy\), o\.own, 4, pad\)\.length > 0/);
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
  // On CyberLogix the Needs you now bar, the front page's first-screen facts
  // and a fridge's status label are kept clear too (the console's search box
  // and tabs since core 1.4.1 by the core itself: it hides rather than cover).
  for (const sel of ["#needs-bar", ".needs-bar-acts", "#facts", ".sensor-top", ".thing-word"]) assert.ok(cfg.includes(`"${sel}"`), sel);
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
  const sandbox = { window: {}, document: {}, URL };
  vm.runInNewContext(fs.readFileSync(core("pilot-panel.js"), "utf8"), sandbox);
  const P = sandbox.window.PilotPanel;
  // Plain values from this realm, so deepEqual compares them fairly.
  const plain = (v) => JSON.parse(JSON.stringify(v));
  return { ...P, welcomePills: (g) => plain(P.welcomePills(g)), hiddenSections: (g) => plain([...P.hiddenSections(g)]) };
}

test("welcome 1.2.0: the design's five pills and tagline unless the app gives its own", () => {
  const P = panelHelpers();
  assert.equal(P.VERSION, "1.4.2");
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
  assert.ok(panel.includes("const pills = welcomePills(config.welcomePills, location.href);"));
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
  // (Core 1.4.2: unless one of the app's own answers clearly fits the words: matchTyped.)
  assert.ok(panel.includes('if (!extra && typedHere(words)) return;\n        if (!extra) hold({ text: words }); else show("settings");'));
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
  // Core 1.3.0: the first look never sits on content; with words in every
  // corner it stays tucked away (its glow plays there).
  assert.equal(Mascot.placeNow({ ...o, reveal: true }).peek, true);
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  assert.ok(loader.includes('M().firstLook(storage("sessionStorage"), `${store.prefix}-hello`, config.revealMs)'));
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
    // Core 1.3.0: never below a 44 px tap target, whatever scale is asked.
    assert.ok(b.right - b.left >= 44 && b.bottom - b.top >= 44 - 0.01, `${c} ${JSON.stringify(b)}`);
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
  assert.deepEqual(Mascot.contentBox(dot, rect(320, 740, 56, 54), null, doc), { left: 330, top: 760, right: 364, bottom: 800, kind: "mark" });
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

test("CyberLogix's own welcome: its tagline, a visitor's five on the public pages and the console's five on its own pages, no picture or video keys, kept on screen, its phone menu", () => {
  const cfg = fs.readFileSync(app("pilot-cyberlogix.js"), "utf8");
  assert.ok(cfg.includes('tagline: "Your fridge-alarm helper",'));
  for (const label of ["Which fridges need me?", "What does this alert mean?", "Is the food still OK?", "How do I connect a sensor?",
    "Will it work for my café?", "What does it cost?", "See it live", "What sensors do I need?", "Add my AI key"]) {
    assert.ok(cfg.includes(`"${label}"`), label);
  }
  assert.ok(cfg.includes('hideKeySections: ["image", "video"],'));
  assert.ok(cfg.includes("keepInViewport: true,"));
  assert.ok(cfg.includes('modals: [".menu[open] > nav"],'));
  // The config's pills come through the core's cleaning as the five given:
  // a visitor's on a public page, the console's on the console and /today.
  // Core 1.4.0: each is a link or the app's own answer (no key needed),
  // but the key pill.
  const sandbox = { window: {}, document: {}, URL };
  vm.runInNewContext(fs.readFileSync(core("pilot-panel.js"), "utf8"), sandbox);
  const kind = (p) => (p.link ? `link ${new URL(p.link).pathname}${new URL(p.link).hash}` : p.answer !== undefined ? "answer" : p.builtin ? p.action : "ai");
  const own = [["Which fridges need me?", "answer"], ["What does this alert mean?", "answer"],
    ["Is the food still OK?", "answer"], ["How do I connect a sensor?", "answer"], ["Add my AI key", "settings"]];
  // 3.32.0: the price and the sensors are answered from the real figures,
  // with their pages linked in the answer.
  const visitor = [["Will it work for my café?", "answer"], ["What does it cost?", "answer"],
    ["See it live", "link /demo"], ["What sensors do I need?", "answer"], ["Add my AI key", "settings"]];
  for (const [pathname, want] of [["/", visitor], ["/pricing", visitor], ["/help", visitor], ["/console", own], ["/today", own]]) {
    let given = null;
    vm.runInNewContext(cfg, { window: { Pilot: { mount: (c) => { given = c; } }, location: { pathname }, localStorage: { getItem: () => null } },
      document: { getElementById: () => ({ dataset: {} }), title: "" } });
    const pills = sandbox.window.PilotPanel.welcomePills(given.welcomePills, `https://cyberlogix.example${pathname}`);
    assert.deepEqual(JSON.parse(JSON.stringify(pills.map((p) => [p.label, kind(p)]))), want, pathname);
    assert.equal(given.keepInViewport, true);
    assert.deepEqual([...sandbox.window.PilotPanel.hiddenSections(given.hideKeySections)], ["image", "video"]);
    // A public page tells the AI it sees nobody's fridges there.
    const sc = given.screen();
    if (pathname === "/") assert.match(sc.facts, /^Not signed in: Pilot can't see their fridges or alerts here\./);
  }
});

// ---------------------------------------------------------------- core 1.3.0: the first look, one key ask, room for the welcome, tap targets

test("core 1.4.2: every core file says 1.4.2", () => {
  const files = fs.readdirSync(path.join(__dirname, "..", "..", "static", "pilot")).filter((n) => /\.(js|css)$/.test(n));
  assert.equal(files.length, 13);
  for (const f of files) {
    assert.match(fs.readFileSync(core(f), "utf8").split("\n")[0], /Pilot core 1\.4\.2 · /, f);
    const v = fs.readFileSync(core(f), "utf8").match(/const VERSION = "([\d.]+)"/);
    if (v) assert.equal(v[1], "1.4.2", f);
  }
  for (const m of [Mascot, Render, Store, Engine, Providers, Media]) assert.equal(m.VERSION, "1.4.2");
});

test("first look 1.3.0: it uses the same free-corner check as normal placement, and never sits on content", () => {
  const rect = (l, t, w, h) => ({ left: l, top: t, right: l + w, bottom: t + h, width: w, height: h });
  // A button in the bottom right corner only (the "Learn more" button, say).
  const btn = { nodeType: 1, tagName: "BUTTON", getBoundingClientRect: () => rect(250, 700, 125, 100) };
  btn.closest = () => btn;
  const win = { innerWidth: 375, innerHeight: 800, getComputedStyle: () => ({ getPropertyValue: () => "" }) };
  const launcher = { offsetWidth: 56, offsetHeight: 54, getBoundingClientRect: () => rect(307, 734, 56, 54) };
  const doc = { body: {}, querySelectorAll: () => [], elementsFromPoint: (x, y) => (x > 250 && y > 700 ? [btn] : []) };
  doc.activeElement = doc.body;
  const o = { doc, win, own: { contains: () => false }, launcher, avoid: [], corner: "br", shown: "br", full: true, text: true };
  // During the first look as at any other time: the free corner, whole.
  assert.deepEqual(Mascot.placeNow({ ...o, reveal: true }), { corner: "bl", peek: false, typing: false });
  assert.deepEqual(Mascot.placeNow(o), { corner: "bl", peek: false, typing: false });
  // Mid-scroll (arriving at /orbyt#pitch scrolls the page): it tucks in place, never shows whole on the button.
  assert.deepEqual(Mascot.placeNow({ ...o, full: false, reveal: true }), { corner: "br", peek: true, typing: false });
  // The things an app asks it to avoid count during the first look too.
  const tile = rect(0, 600, 375, 200);
  const avoidDoc = { ...doc, elementsFromPoint: () => [], querySelectorAll: () => [{ getClientRects: () => [tile], getBoundingClientRect: () => tile, closest: () => null }] };
  avoidDoc.activeElement = avoidDoc.body;
  const placed = Mascot.placeNow({ ...o, doc: avoidDoc, win: { ...win, getComputedStyle: () => ({ getPropertyValue: () => "", visibility: "visible", display: "block", opacity: "1" }) },
    avoid: [".tile"], reveal: true });
  assert.equal(placed.corner[0], "t");
  assert.equal(placed.peek, false);
  // The loader no longer forces the robot out during the first look; it only glows.
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  assert.ok(!loader.includes("revealUntil"));
  assert.ok(loader.includes('rootEl.classList.add("pilot-revealing")'));
  const mascot = fs.readFileSync(core("pilot-mascot.js"), "utf8");
  assert.ok(!/o\.reveal/.test(mascot), "placeNow has no first-look exception");
});

test("welcome 1.3.0: an app's own key pill means no key card as well; the design's five keep their card", () => {
  const P = panelHelpers();
  const site = [{ icon: "spark", label: "Help with my idea", action: "My idea: " }, { icon: "key", label: "Add my AI key", action: "settings" }];
  assert.equal(P.keyPillGiven(site), true);
  assert.equal(P.keyPillGiven(undefined), false); // the design's five: the card stays
  assert.equal(P.keyPillGiven([{ icon: "chat", label: "Ask", action: "ask" }]), false); // app pills with no key pill: card stays
  // A pill whose prompt merely says "settings" with words around it isn't the built-in.
  assert.equal(P.keyPillGiven([{ label: "Settings help", action: "Tell me about settings" }]), false);
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  assert.ok(panel.includes("store.ready() || oneKeyAsk ? null : keyCard()"));
  assert.ok(panel.includes("const oneKeyAsk = keyPillGiven(config.welcomePills);"));
});

test("welcome 1.3.0: websites say 'AI key'; the design (and Pilot for Mac) keeps 'Bring Your Own API Key' exactly", () => {
  const P = panelHelpers();
  assert.equal(P.keyWords(undefined).title, "Bring Your Own API Key");
  assert.equal(P.keyWords(undefined).sub, "Use your OpenAI, Claude, Gemini, or any compatible API key.");
  const site = P.keyWords([{ icon: "key", label: "Add my AI key", action: "settings" }]);
  for (const w of Object.values(site)) assert.ok(!/\bAPI key\b/i.test(w), w);
  assert.match(site.title, /AI key/);
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  // No key wording is written outside the two word sets.
  const outside = panel.replace(/const KEY_WORDS = \{[\s\S]*?\n  \};/, "");
  assert.ok(!outside.includes("Bring Your Own API Key"));
  assert.ok(!outside.includes("Paste your API key"));
});

test("welcome 1.3.0/1.4.1: a one-line grown-ups note when the app gives one (always, with More); the full note stays elsewhere", () => {
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  // 1.4.1: the short form whenever the app gives one, not only on the welcome; the design's full note otherwise.
  assert.ok(panel.includes('const short = typeof config.keyNoteShort === "string" && config.keyNoteShort.trim() ? config.keyNoteShort.trim() : "";'));
  assert.ok(panel.includes('const shows = !short ? "full" : noteOpen ? "open" : "short";'));
  assert.ok(panel.includes('if (shows === "full") { footNote.textContent = config.keyNote; footNote.removeAttribute("title"); return; }'));
  assert.ok(panel.includes('text: shows === "open" ? "Less" : "More", on: { click: () => { noteOpen = !noteOpen; drawFootNote(); } } });'));
  assert.ok(panel.includes('if (shows === "short") footNote.setAttribute("title", config.keyNote);'));
  // Settings and help keep the full note.
  assert.equal((panel.match(/config\.keyNote \? el\("p", \{ class: "pilot-grownup", text: config\.keyNote \}\)/g) || []).length, 2);
  const css = fs.readFileSync(core("pilot-panel.css"), "utf8");
  assert.match(css, /\.pilot-foot-note-short \.pilot-foot-note-words \{ min-width: 0; overflow: hidden; text-overflow: ellipsis; \}/);
  // Shorter screens get the tighter welcome: desktops up to 900 px tall, phones up to 760 px.
  assert.match(css, /@media \(min-width: 721px\) and \(max-height: 900px\)/);
  assert.match(css, /@media \(max-width: 720px\) and \(max-height: 760px\)/);
  // This app's short line keeps the meaning: an AI key is for grown-ups.
  const cfg = fs.readFileSync(app("pilot-cyberlogix.js"), "utf8");
  assert.ok(cfg.includes('keyNoteShort: "AI keys are for grown-ups (18+).",'));
});

test("tap target 1.3.0: the tucked-away robot is never smaller than 44 px each way", () => {
  assert.equal(Mascot.TAP_LEAST, 44);
  // Phones (56 x 54) and computers (88 x 85): whatever scale is asked.
  for (const size of [{ width: 56, height: 54 }, { width: 88, height: 85 }]) {
    for (const scale of [0.2, 0.42, 0.5, undefined]) {
      const s = Mascot.keepScale(scale, size);
      assert.ok(size.width * s >= 44 && size.height * s >= 44 - 1e-9, `${JSON.stringify(size)} ${scale} -> ${s}`);
    }
  }
  assert.equal(Mascot.keepScale(0.9, { width: 88, height: 85 }), 0.9); // bigger asks are kept
  assert.equal(Mascot.keepScale(0.5, { width: 30, height: 30 }), 1); // never bigger than the robot
  // pilot.css's own scales give 44 px too (the robot is drawn 0.97 as tall as wide).
  const css = fs.readFileSync(core("pilot.css"), "utf8");
  const desk = Number(css.match(/--pilot-peek-scale: ([\d.]+);/)[1]);
  const phone = Number(css.match(/@media \(max-width: 720px\) \{\s*\.pilot-root \{[^}]*--pilot-peek-scale: ([\d.]+);/)[1]);
  assert.ok(88 * 0.97 * desk >= 44, `desktop ${desk}`);
  assert.ok(56 * 0.97 * phone >= 44, `phone ${phone}`);
});

test("first look 1.3.0: with no corner free, it tucks where it covers least, not just in its own corner", () => {
  const view = { width: 375, height: 800 };
  const size = { width: 56, height: 54 };
  const keep = { scale: 0.42, margin: 3 };
  // Everything is busy; the bottom left covers least.
  const cost = (box, c) => ({ br: 400, bl: 30, tr: 900, tl: 900 })[c];
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, busy: () => true, cost, keep }), { corner: "bl", peek: true });
  // Without a cost (an older caller): its own corner, as before.
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, busy: () => true, keep }), { corner: "br", peek: true });
  // A box being typed in still rules out a corner, however little it covers.
  const field = { left: 0, top: 700, right: 375, bottom: 800 };
  assert.equal(Mascot.choosePlace({ corner: "br", view, size, gap: 12, busy: () => true, cost, keep, hard: [field] }).corner[0], "t");
  assert.equal(Mascot.areaOf({ left: 0, top: 0, right: 10, bottom: 10 }, { left: 5, top: 5, right: 20, bottom: 20 }), 25);
});

// ---------------------------------------------------------------- core 1.4.0: useful before a key, and phones' top corners last

// A phone page for placeNow: a header that stays put (sticky) with a small
// "Home" link at the top right, words along the bottom, and (optionally) a
// big card link across the bottom corners. Elements are plain objects.
function phonePage({ cardAtBottom = false, logo = false, width = 375, height = 812 } = {}) {
  const rect = (l, t, w, h) => ({ left: l, top: t, right: l + w, bottom: t + h, width: w, height: h });
  const header = { nodeType: 1, tagName: "HEADER", childNodes: [], parentElement: null, style: { position: "sticky" },
    getBoundingClientRect: () => rect(0, 0, width, 53), closest: () => null };
  const home = { nodeType: 1, tagName: "A", childNodes: [], parentElement: header, style: { position: "static" },
    getBoundingClientRect: () => rect(width - 49, 7, 47, 40) };
  home.closest = (sel) => (sel === Mascot.INTERACTIVE ? home : null);
  // The site's logo, a link home, at the top left (optional).
  const brand = { nodeType: 1, tagName: "A", childNodes: [], parentElement: header, style: { position: "static" },
    getBoundingClientRect: () => rect(12, 6, 200, 42) };
  brand.closest = (sel) => (sel === Mascot.INTERACTIVE ? brand : null);
  const lines = [];
  for (let y = height - 120; y < height; y += 22) lines.push(rect(16, y, width - 32, 18));
  const para = { nodeType: 1, tagName: "P", parentElement: null, style: { position: "static" }, closest: () => null,
    getBoundingClientRect: () => rect(16, height - 120, width - 32, 120), childNodes: [{ nodeType: 3, nodeValue: "words", rects: lines }] };
  const card = { nodeType: 1, tagName: "A", childNodes: [], parentElement: null, style: { position: "static" },
    getBoundingClientRect: () => rect(30, height - 110, width - 60, 220) };
  card.closest = (sel) => (sel === Mascot.INTERACTIVE ? card : null);
  const win = { innerWidth: width, innerHeight: height,
    getComputedStyle: (el) => ({ getPropertyValue: () => "", position: (el && el.style && el.style.position) || "static" }) };
  const doc = { body: {}, defaultView: win, querySelectorAll: () => [],
    createRange: () => ({ selectNodeContents(n) { this.n = n; }, getClientRects() { return (this.n && this.n.rects) || []; } }),
    elementsFromPoint: (x, y) => {
      if (y < 53 && logo && x > 12 && x < 212 && y > 6 && y < 48) return [brand, header];
      if (y < 53) return x > width - 49 && y > 7 && y < 47 ? [home, header] : [header];
      // The card's words are spans inside the link: a different element at each point.
      if (cardAtBottom && y > height - 110 && x > 30 && x < width - 30) {
        const words = { nodeType: 1, tagName: "SPAN", childNodes: [], parentElement: card, style: { position: "static" }, getBoundingClientRect: card.getBoundingClientRect };
        words.closest = (sel) => (sel === Mascot.INTERACTIVE ? card : null);
        return [words, card];
      }
      if (y > height - 120) return [para];
      return [];
    } };
  doc.activeElement = doc.body;
  const launcher = { offsetWidth: 56, offsetHeight: 54, getBoundingClientRect: () => rect(width - 68, height - 66, 56, 54) };
  return { doc, win, own: { contains: () => false }, launcher, avoid: [], corner: "br", shown: "br", full: true, text: true, keepIn: true };
}

test("placement 1.4.0: on a phone it never parks top-left over the header (CyberLogix's 3,3), it tucks at the bottom", () => {
  const place = Mascot.placeNow(phonePage());
  // Core 1.3.0 put it top-left: the header's empty corner looked free.
  assert.equal(place.corner[0], "b", JSON.stringify(place));
  assert.equal(place.peek, true);
  // The header that stays put counts as busy: it is a "pinned" thing.
  const p = phonePage();
  const hits = Mascot.contentIn(p.doc, { left: 3, top: 3, right: 49, bottom: 48 }, p.own);
  assert.deepEqual(hits.map((h) => h.kind), ["pinned"]);
});

test("placement 1.4.0: it never tucks over a small 'Home' link while a corner avoids one", () => {
  const place = Mascot.placeNow(phonePage({ cardAtBottom: true, logo: true }));
  // Core 1.3.0 chose the top right (the Home link covered less area than the card's corner).
  // Core 1.4.1: every corner has a link here, so it hides, keeping its bottom corner.
  assert.equal(place.corner[0], "b", JSON.stringify(place));
  const small = { left: 326, top: 7, right: 373, bottom: 47, kind: "control" };
  const big = { left: 30, top: 702, right: 345, bottom: 922, kind: "control" };
  const slice = { left: 326, top: 764, right: 372, bottom: 809 };
  const topSlice = { left: 326, top: 3, right: 372, bottom: 48 };
  // A small link covered mostly costs about as much as a big card under part of the robot (1.4.1: the robot's share counts).
  assert.ok(Mascot.costOf([small], topSlice) >= Mascot.costOf([big], slice) * 0.9);
  // Any link or button costs more than a corner's worth of words.
  const words = { left: 0, top: 700, right: 375, bottom: 812, kind: "text" };
  assert.ok(Mascot.costOf([big], slice) > Mascot.costOf([words], slice) * 10);
  // The same thing found at several points of the grid counts once.
  const p = phonePage({ cardAtBottom: true });
  assert.equal(Mascot.contentIn(p.doc, slice, p.own).filter((h) => h.kind === "control").length, 1);
});

test("placement 1.4.0: phones try the bottom corners first, whole and tucked, before any top corner; computers as before", () => {
  const view = { width: 375, height: 812 };
  const size = { width: 56, height: 54 };
  const keep = { scale: 0.82, margin: 3 };
  assert.equal(Mascot.isNarrow(view), true);
  assert.equal(Mascot.isNarrow({ width: 1366, height: 900 }), false);
  assert.deepEqual(Mascot.cornerPasses("br", true), [["br", "bl"], ["tr", "tl"]]);
  assert.deepEqual(Mascot.cornerPasses("tl", true), [["tl", "tr", "bl", "br"]], "a corner the visitor chose stays first");
  assert.deepEqual(Mascot.cornerPasses("br", false), [["br", "bl", "tr", "tl"]]);
  // Whole bottom corners busy, the top right free whole: on a phone a tucked bottom corner comes first.
  const wholeBusyBottom = (b, c, small) => c[0] === "b" && !small;
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, busy: wholeBusyBottom, keep, narrow: true }), { corner: "br", peek: true });
  // Core 1.3.0's order (and a computer's): the free top corner, whole.
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, busy: wholeBusyBottom, keep }), { corner: "tr", peek: false });
  // Bottom corners over a few words, a top corner over nothing: still the bottom, on a phone.
  const wordsBelow = (b, c) => c[0] === "b";
  const cost = (b, c) => (c[0] === "b" ? 900 : 0);
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, busy: wordsBelow, cost, keep, narrow: true }), { corner: "br", peek: true });
  // ...but not over a link or button: then the clear top corner.
  const linkBelow = (b, c) => (c[0] === "b" ? 1e5 : 0);
  assert.equal(Mascot.choosePlace({ corner: "br", view, size, gap: 12, busy: wordsBelow, cost: linkBelow, keep, narrow: true }).corner[0], "t");
  // Everything busy, the top cheaper: a phone still tucks at the bottom; a computer takes the cheaper top.
  const topCheaper = (b, c) => (c[0] === "t" ? 100 : 2000);
  assert.equal(Mascot.choosePlace({ corner: "br", view, size, gap: 12, busy: () => true, cost: topCheaper, keep, narrow: true }).corner[0], "b");
  assert.equal(Mascot.choosePlace({ corner: "br", view: { width: 1366, height: 900 }, size, gap: 12, busy: () => true, cost: topCheaper, keep }).corner[0], "t");
  // A box being typed in along the bottom still sends it to the top, on a phone too.
  const field = { left: 0, top: 700, right: 375, bottom: 812 };
  assert.equal(Mascot.choosePlace({ corner: "br", view, size, gap: 12, busy: () => true, cost: topCheaper, keep, hard: [field], narrow: true }).corner[0], "t");
});

test("placement 1.4.0: a header or bar that stays put is busy; a whole-screen fixed layer is not a bar", () => {
  const W = { innerWidth: 375, innerHeight: 812, getComputedStyle: (el) => ({ position: el.style.position }) };
  const bar = { nodeType: 1, tagName: "NAV", style: { position: "fixed" }, getBoundingClientRect: () => box(0, 762, 375, 50), parentElement: null };
  const tab = { nodeType: 1, tagName: "SPAN", style: { position: "static" }, parentElement: bar };
  assert.equal(Mascot.pinnedBar(tab, null, W, new Map()), bar);
  const frame = { nodeType: 1, tagName: "DIV", style: { position: "fixed" }, getBoundingClientRect: () => box(0, 0, 375, 812), parentElement: null };
  const inFrame = { nodeType: 1, tagName: "DIV", style: { position: "static" }, parentElement: frame };
  assert.equal(Mascot.pinnedBar(inFrame, null, W, new Map()), null);
  const plain = { nodeType: 1, tagName: "DIV", style: { position: "static" }, parentElement: null };
  assert.equal(Mascot.pinnedBar(plain, null, W), null);
  // Pilot's own root is never a bar.
  assert.equal(Mascot.pinnedBar(tab, { contains: (n) => n === bar || n === tab }, W), null);
});

test("first look 1.4.0: on a small screen it plays tucked away, and stays tucked until the visitor scrolls", () => {
  assert.equal(Mascot.isCompact({ width: 320, height: 640 }), true);
  assert.equal(Mascot.isCompact({ width: 375, height: 667 }), true);
  assert.equal(Mascot.isCompact({ width: 375, height: 812 }), false);
  assert.equal(Mascot.isCompact({ width: 1440, height: 900 }), false);
  const view = { width: 320, height: 640 };
  const size = { width: 56, height: 54 };
  const keep = { scale: 0.82, margin: 3 };
  // A free corner: whole normally, tucked with `tuck`.
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, keep, narrow: true }), { corner: "br", peek: false });
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 12, keep, narrow: true, tuck: true }), { corner: "br", peek: true });
  const p = phonePage({ width: 320, height: 640 });
  p.doc.elementsFromPoint = () => [];
  assert.equal(Mascot.placeNow(p).peek, false);
  assert.equal(Mascot.placeNow({ ...p, tuck: true }).peek, true);
  assert.equal(Mascot.placeNow({ ...p, tuck: true, full: false }).peek, true);
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  assert.ok(loader.includes("keepIn: Boolean(config.keepInViewport), tuck: quiet, "));
  assert.ok(loader.includes("quiet = M().isCompact({ width: window.innerWidth, height: window.innerHeight });"));
  assert.ok(loader.includes("scrolling = setTimeout(() => { scrolling = 0; quiet = false; queueFit(true); }, 160);"));
});

test("pills 1.4.0: a link to a page of this site, or the app's own answer; other sites and empty answers are left out", () => {
  const P = panelHelpers();
  const base = "https://mtrx.example/genesis";
  const pills = JSON.parse(JSON.stringify(P.ownPills([
    { icon: "spark", label: "See it live", link: "/demo" },
    { icon: "chat", label: "Elsewhere", link: "https://evil.example/demo" },
    { icon: "chat", label: "Sneaky", link: "//evil.example/x" },
    { icon: "chat", label: "Script", link: "javascript:alert(1)" },
    { icon: "bolt", label: "What does it cost?", answer: "From **$9** a month. [Pricing](/pricing)" },
    { icon: "bolt", label: "Empty", answer: "   " },
    { icon: "key", label: "Add my AI key", action: "settings" },
  ], base)));
  assert.deepEqual(pills.map((p) => p.label), ["See it live", "What does it cost?", "Add my AI key"]);
  assert.equal(pills[0].link, "https://mtrx.example/demo");
  assert.equal(pills[0].builtin, false);
  assert.equal(pills[1].answer, "From **$9** a month. [Pricing](/pricing)");
  // A function answer is kept as given (called when pressed).
  const fn = () => "hi";
  const raw = P.ownPills([{ label: "Apps", answer: fn }], base);
  assert.equal(raw[0].answer, fn);
  assert.equal(P.siteLink("#faq", base), "https://mtrx.example/genesis#faq");
  assert.equal(P.siteLink("https://mtrx.example/orbyt", base), "https://mtrx.example/orbyt");
  assert.equal(P.siteLink("http://mtrx.example/orbyt", base), "");
  // Suggestions take them too, and keep plain prompts as before.
  const chips = JSON.parse(JSON.stringify(P.cleanChips([{ label: "Live", link: "/demo" }, { label: "Away", link: "https://x.example" },
    { label: "Ask", prompt: "Explain" }, { label: "Cost", answer: "Free." }, { label: "Fourth", prompt: "x" }], base)));
  assert.deepEqual(chips, [{ label: "Live", link: "https://mtrx.example/demo" }, { label: "Ask", prompt: "Explain" },
    { label: "Cost", answer: "Free." }]);
  // The design's five are unchanged, and a key pill still means one key ask.
  assert.equal(P.welcomePills(undefined).length, 5);
  assert.equal(P.keyPillGiven([{ label: "Live", link: "/demo" }, { label: "Key", action: "settings" }]), true);
});

test("pills 1.4.0: pressing one that needs no AI never asks for a key and sends nothing; the waiting-message flow is for AI only", () => {
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  // A no-AI pill or suggestion is handled before anything that could hold for a key.
  assert.match(panel, /function runPill\(p\) \{\s*const s = screen\(\);\s*if \(plainRun\(p, p\.label\)\) return;/);
  assert.ok(panel.includes("const runChip = (c) => { if (!plainRun(c, c.label)) ask(c.prompt || c.label); };"));
  const answerFn = panel.slice(panel.indexOf("async function answerWith"), panel.indexOf("// A suggestion or pill that needs no AI"));
  assert.ok(answerFn.length > 200);
  for (const never of ["hold(", "engine.send", "fetch", "show(\"settings\")", "ask("]) assert.ok(!answerFn.includes(never), never);
  // It is shown through the chat (safe renderer), marked as the app's own, with the key offered under it.
  assert.ok(answerFn.includes('{ role: "assistant", text: lead ? `${lead}\\n\\n${text}` : text, local: true }'));
  assert.ok(answerFn.includes('{ role: "note", kind: "more", text: more }'));
  assert.ok(answerFn.includes("!store.ready()"));
  assert.ok(panel.includes("const opts = m.local ? { local: location.href } : undefined;"));
  assert.ok(panel.includes("body.append(R.render(m.text, document, opts));"));
  assert.equal(panelHelpers().DEFAULT_MORE, "Want more? Add your AI key to ask anything.");
  // Nothing in the window is ever set as HTML.
  assert.ok(!/innerHTML|insertAdjacentHTML|outerHTML/.test(panel));
});

test("render 1.4.0: an app's own answer may link to this site's pages (same tab); AI answers can't, and stay as before", () => {
  const base = "https://mtrx.example/genesis";
  const text = "Read [Orbyt](/orbyt), [our docs](https://docs.example/x), [sneaky](//evil.example) and [bad](javascript:alert(1)).";
  // An AI answer: a path is just words; an outside link opens in a new tab.
  const ai = Render.parse(text);
  const aiLinks = ai[0].c.filter((p) => p.t === "a");
  assert.deepEqual(aiLinks.map((a) => a.href), ["https://docs.example/x"]);
  // The app's own answer: the path is a link too; "//elsewhere" and javascript: never.
  const own = Render.parse(text, { local: true });
  assert.deepEqual(own[0].c.filter((p) => p.t === "a").map((a) => a.href), ["/orbyt", "https://docs.example/x"]);
  assert.equal(Render.safeHref("/orbyt", base), "https://mtrx.example/orbyt");
  assert.equal(Render.safeHref("javascript:alert(1)", base), null);
  assert.equal(Render.sameSite("https://mtrx.example/orbyt", base), true);
  assert.equal(Render.sameSite("https://docs.example/x", base), false);
  assert.equal(Render.sameSite("https://mtrx.example/orbyt", ""), false);
  // Rendered: this site's link stays in this tab, the outside one opens a new tab without the page's address.
  const links = walk(Render.render(text, fakeDocument(), { local: base })).filter((n) => n.tagName === "a");
  assert.equal(links.length, 2);
  assert.equal(links[0].attrs.href, "https://mtrx.example/orbyt");
  assert.equal(links[0].attrs.target, undefined);
  assert.equal(links[1].attrs.target, "_blank");
  assert.match(links[1].attrs.rel, /noopener noreferrer/);
  // Without `local` (every AI answer), exactly as before: no path links.
  assert.equal(walk(Render.render(text, fakeDocument())).filter((n) => n.tagName === "a").length, 1);
  assert.equal(Render.plain("See [Orbyt](/orbyt).", { local: true }), "See Orbyt.");
});

test("store 1.4.0: an app's own answer and the key offer under it survive the trip through storage", () => {
  assert.deepEqual(Store.cleanMessage({ role: "assistant", text: "Hi", local: true }), { role: "assistant", text: "Hi", local: true });
  assert.deepEqual(Store.cleanMessage({ role: "assistant", text: "Hi", local: "yes" }), { role: "assistant", text: "Hi" });
  assert.deepEqual(Store.cleanMessage({ role: "note", kind: "more", text: "Want more?" }), { role: "note", kind: "more", text: "Want more?" });
  assert.equal(Store.cleanMessage({ role: "note", kind: "html", text: "x" }).kind, "info");
});

// The MTRX site's own 1.4.0 pills (Genesis's start form, Orbyt, its app
// list) are tested in that repo. This app's link and answer pills are tested
// above (CyberLogix's own welcome) and in tests/js/cyberlogix-pilot.test.js.

test("wandering 1.4.0: something to avoid holds the robot still only where it strolls, not anywhere on the page", () => {
  const W = { innerWidth: 375, innerHeight: 812 };
  const launcher = { offsetHeight: 54 };
  // A badge far up the page (or off screen) no longer keeps it still for good.
  assert.equal(Mascot.inStroll([box(20, 120, 60, 20), box(20, 2400, 60, 20)], W, launcher), false);
  // One along the bottom of the screen does.
  assert.equal(Mascot.inStroll([box(20, 760, 60, 20)], W, launcher), true);
  assert.equal(Mascot.inStroll([], W, launcher), false);
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  assert.ok(loader.includes("const avoid = () => [...list(config.avoid), ...list(config.yieldTo)];"));
  assert.ok(loader.includes("M().inStroll(M().boxesOf(document, avoid(), rootEl, window), window, launcher)"));
});

// ---------------------------------------------------------------- core 1.4.1: hide rather than cover, one bottom corner while scrolling, the welcome stays

test("placement 1.4.1: typing low on a phone with a sticky header, it fades out instead of parking at 3,3; back when typing ends", () => {
  const rect = (l, t, w, h) => ({ left: l, top: t, right: l + w, bottom: t + h, width: w, height: h });
  const p = phonePage();
  // Genesis's "today's spark" box, low on the screen (round-7: robot=3,3 over the logo while typing).
  const field = { nodeType: 1, tagName: "INPUT", type: "text", getBoundingClientRect: () => rect(47, 766, 281, 46) };
  p.doc.activeElement = field;
  const typing = Mascot.placeNow(p);
  assert.equal(typing.typing, true);
  assert.equal(typing.hide, true, JSON.stringify(typing));
  assert.equal(typing.corner[0], "b", "hidden, it keeps its bottom corner, so it comes back there");
  // With room at the top (no header that stays put, nothing there), it may still sit there while typing.
  const open = phonePage();
  open.doc.activeElement = field;
  const words = open.doc.elementsFromPoint;
  open.doc.elementsFromPoint = (x, y) => (y < 53 ? [] : words(x, y));
  const free = Mascot.placeNow(open);
  assert.equal(free.hide, undefined, JSON.stringify(free));
  assert.equal(free.corner[0], "t");
  // Typing done: back in its bottom corner, shown.
  p.doc.activeElement = p.doc.body;
  const after = Mascot.placeNow(p);
  assert.equal(after.hide, undefined, JSON.stringify(after));
  assert.equal(after.corner[0], "b");
  // An app can keep 1.4.0's tucking over the least (config.hideWhenCrowded: false).
  p.doc.activeElement = field;
  assert.equal(Mascot.placeNow({ ...p, away: false }).hide, undefined);
});

test("placement 1.4.1: a box being typed in under every corner: it hides rather than cover the box", () => {
  const view = { width: 1366, height: 900 };
  const size = { width: 88, height: 85 };
  const field = { left: 0, top: 0, right: 1366, bottom: 900 };
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 16, hard: [field], busy: () => true, cost: () => 10, away: true }),
    { corner: "br", peek: true, hide: true });
  // Words only under the tucked robot (no link, bar or box to avoid): it tucks, as before.
  const low = { left: 0, top: 800, right: 1366, bottom: 900 };
  assert.deepEqual(Mascot.choosePlace({ corner: "br", view, size, gap: 16, hard: [low], busy: () => true, cost: () => 10, away: true }),
    { corner: "tr", peek: true });
});

test("placement 1.4.1: every corner blocked on a 320 px phone (sticky bar on top, search and tabs below): it hides, never tucks over the search box", () => {
  const view = { width: 320, height: 640 };
  const size = { width: 56, height: 54 };
  const keep = { scale: 0.82, margin: 3 };
  // CyberLogix's console at y=500: .finder and .f-tab (in `avoid`) fill the bottom strip...
  const finder = { left: 0, top: 590, right: 200, bottom: 640 };
  const tab = { left: 200, top: 596, right: 320, bottom: 640 };
  // ...and the "Needs you now" bar (sticky, also avoided) the top.
  const bar = { left: 0, top: 0, right: 320, bottom: 56 };
  const cost = (b, c) => (c[0] === "t" ? Mascot.WEIGHT.pinned + 900 : 1500);
  const o = { corner: "br", view, size, gap: 12, keep, blocks: [finder, tab, bar], busy: () => true, cost, narrow: true };
  // Core 1.4.0 tucked bottom-left over the search box's end.
  assert.deepEqual(Mascot.choosePlace({ ...o, away: true }), { corner: "br", peek: true, hide: true });
  assert.deepEqual(Mascot.choosePlace({ ...o, away: true, stay: "bl" }), { corner: "bl", peek: true, hide: true });
  // Without `away` (an app's hideWhenCrowded: false), 1.4.0's choice.
  assert.equal(Mascot.choosePlace(o).hide, undefined);
});

test("placement 1.4.1: a big link card under the resting robot counts as fully covered; every corner on a link: hidden at the top of the page", () => {
  // Orbyt's "Partner with us" path card under the bottom-left corner (375x667).
  const card = { left: 0, top: 560, right: 375, bottom: 760, kind: "control" };
  const slice = { left: 3, top: 619, right: 49, bottom: 664 };
  assert.ok(Mascot.costOf([card], slice) >= Mascot.WEIGHT.control, String(Mascot.costOf([card], slice)));
  // A link that only touches the robot's edge still costs its small share.
  const edge = { left: 45, top: 600, right: 375, bottom: 700, kind: "control" };
  const part = Mascot.costOf([edge], slice);
  assert.ok(part >= Mascot.WEIGHT.control * 0.1 && part < Mascot.WEIGHT.control * 0.2, String(part));
  // Big cards across the bottom, a header that stays put with a Home link on top: nowhere is free of a link.
  const p = phonePage({ cardAtBottom: true, width: 375, height: 667 });
  const place = Mascot.placeNow(p);
  assert.equal(place.hide, true, JSON.stringify(place));
  // On a computer, never hidden for this (it tucks where it covers least, as before).
  const wide = phonePage({ cardAtBottom: true, width: 1366, height: 900 });
  assert.equal(Mascot.placeNow(wide).hide, undefined);
});

test("placement 1.4.1: on a phone once scrolled, one bottom corner, tucked, kept while words pass under it; it moves only off a link", () => {
  const view = { width: 375, height: 812 };
  const size = { width: 56, height: 54 };
  const keep = { scale: 0.82, margin: 3 };
  const base = { corner: "br", view, size, gap: 12, keep, full: true, away: true };
  // Words under the bottom left, nothing under the bottom right: it stays where it is (no hopping).
  const words = (b, c) => (c === "bl" ? 900 : 0);
  assert.deepEqual(Mascot.scrollPlace({ ...base, stay: "bl", cost: words }), { corner: "bl", peek: true });
  // Never whole, never a top corner just because it's free.
  assert.deepEqual(Mascot.scrollPlace({ ...base, stay: "br", cost: () => 0, busy: () => false }), { corner: "br", peek: true });
  // A top corner it sat in before scrolling: back to the bottom.
  assert.deepEqual(Mascot.scrollPlace({ ...base, stay: "tr", cost: () => 0 }), { corner: "br", peek: true });
  // A link comes under it: once scrolling stops, the other bottom corner.
  const linkLeft = (b, c) => (c === "bl" ? Mascot.WEIGHT.control : 0);
  assert.deepEqual(Mascot.scrollPlace({ ...base, stay: "bl", cost: linkLeft }), { corner: "br", peek: true });
  // While the page is still moving it doesn't change corner: it fades out until the link has passed.
  assert.deepEqual(Mascot.scrollPlace({ ...base, full: false, stay: "bl", cost: linkLeft }), { corner: "bl", peek: true, hide: true });
  // Links under both bottom corners: hidden until there is room, even with a free top corner (no hopping to the top).
  const linksBelow = (b, c) => (c[0] === "b" ? Mascot.WEIGHT.control : 0);
  assert.deepEqual(Mascot.scrollPlace({ ...base, stay: "br", cost: linksBelow, busy: () => false }), { corner: "br", peek: true, hide: true });
  // Something the app asked it to avoid counts like a link.
  const avoid = { left: 0, top: 740, right: 60, bottom: 812 };
  assert.deepEqual(Mascot.scrollPlace({ ...base, stay: "bl", cost: () => 0, blocks: [avoid] }), { corner: "br", peek: true });
  // A visitor who dragged it to the top keeps the top edge.
  assert.deepEqual(Mascot.scrollPlace({ ...base, corner: "tl", stay: "tl", cost: () => 0 }), { corner: "tl", peek: true });
});

test("placement 1.4.1: placeNow keeps the phone robot in its bottom corner while scrolled; computers and the top of the page as before", () => {
  const p = phonePage({ width: 375, height: 667 });
  // Scrolled, words under the bottom corners, the header free of nothing: tucked in its own bottom corner.
  for (const shown of ["br", "bl"]) {
    const place = Mascot.placeNow({ ...p, shown, scrolled: true });
    assert.deepEqual(place, { corner: shown, peek: true, typing: false });
  }
  // Mid-scroll too.
  assert.deepEqual(Mascot.placeNow({ ...p, shown: "bl", scrolled: true, full: false }), { corner: "bl", peek: true, typing: false });
  // Nothing on the page at all: still tucked while scrolled (1.4.0: whole, wherever was free).
  const empty = phonePage({ width: 375, height: 667 });
  empty.doc.elementsFromPoint = () => [];
  assert.deepEqual(Mascot.placeNow({ ...empty, scrolled: true }), { corner: "br", peek: true, typing: false });
  assert.deepEqual(Mascot.placeNow(empty), { corner: "br", peek: false, typing: false });
  // A computer ignores `scrolled`.
  const wide = phonePage({ width: 1366, height: 900 });
  wide.doc.elementsFromPoint = () => [];
  assert.deepEqual(Mascot.placeNow({ ...wide, scrolled: true }), { corner: "br", peek: false, typing: false });
});

test("placement 1.4.1: the loader hides the robot when told, keeps it hidden from wandering, notices scrolling away from the top, and opens anyway", () => {
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  assert.ok(loader.includes('scrolled: scrolledDown, away: config.hideWhenCrowded !== false });'));
  assert.ok(loader.includes('rootEl.classList.toggle("pilot-away", Boolean(place.hide));'));
  assert.ok(loader.includes('["pilot-yield", "pilot-away", "pilot-peek", "pilot-typing"]'));
  assert.match(loader, /async function open\(\) \{[^}]*rootEl\.classList\.remove\("pilot-away"\);/);
  assert.ok(loader.includes("if (!(t && t.nodeType === 1 && rootEl.contains(t))) scrolledDown = M().scrolledAway(window, document, t);"));
  assert.ok(loader.includes("scrolledDown = M().scrolledAway(window, document, null);"));
  // Scrolled away: the window past 8 px, or a box at least half the screen tall that scrolls in its place.
  const W = { innerHeight: 800, scrollY: 0 };
  const D = { documentElement: { nodeType: 1 }, body: { nodeType: 1 } };
  assert.equal(Mascot.scrolledAway(W, D, null), false);
  assert.equal(Mascot.scrolledAway({ ...W, scrollY: 40 }, D, null), true);
  assert.equal(Mascot.scrolledAway(W, D, { nodeType: 1, clientHeight: 700, scrollTop: 300 }), true);
  assert.equal(Mascot.scrolledAway(W, D, { nodeType: 1, clientHeight: 120, scrollTop: 300 }), false, "a small box scrolling sideways or a list isn't the page");
  assert.equal(Mascot.scrolledAway(W, D, D.documentElement), false);
  // pilot.css: hidden, and taps go through; only the robot, never an open chat window.
  const css = fs.readFileSync(core("pilot.css"), "utf8");
  assert.match(css, /\.pilot-root\.pilot-away \.pilot-launcher \{ opacity: 0; visibility: hidden; pointer-events: none;/);
  assert.ok(!/\.pilot-away[^{]*\.pilot-panel/.test(css));
});

test("welcome 1.4.1: after the app's own answer the welcome's pills come back in a compact row (not after AI answers)", () => {
  const P = panelHelpers();
  const pills = P.ownPills([
    { icon: "spark", label: "Help with my idea", link: "/genesis#new" },
    { icon: "chat", label: "What is Orbyt?", answer: "Orbyt is..." },
    { icon: "bars", label: "Explain this page", action: "explain-screen" },
    { icon: "bolt", label: "What apps are coming?", answer: "Three apps." },
    { icon: "key", label: "Add my AI key", action: "settings" },
  ], "https://mtrx.example/");
  const again = (messages, keyPill) => JSON.parse(JSON.stringify(P.againPills(messages, pills, keyPill))).map((p) => p.label);
  const local = [{ role: "user", text: "What is Orbyt?" }, { role: "assistant", text: "Orbyt is...", local: true }];
  // The one just asked and the key pill (the line under the answer offers the key) are left out.
  assert.deepEqual(again(local), ["Help with my idea", "Explain this page", "What apps are coming?"]);
  assert.deepEqual(again([...local, { role: "note", kind: "more", text: "Want more?" }]), ["Help with my idea", "Explain this page", "What apps are coming?"]);
  // Under a later answer (no "Want more?" line there: it shows once per chat), the key pill is kept while there is no key.
  assert.deepEqual(again(local, true), ["Help with my idea", "Explain this page", "What apps are coming?", "Add my AI key"]);
  // Not under an AI answer, an error note, or an empty chat; not once the chat has moved on.
  assert.deepEqual(again([{ role: "user", text: "Hi" }, { role: "assistant", text: "Hello" }]), []);
  assert.deepEqual(again([{ role: "user", text: "What is Orbyt?" }, { role: "note", kind: "error", text: "Pilot couldn't find that answer" }]), []);
  assert.deepEqual(again([]), []);
  assert.deepEqual(again([...local, { role: "user", text: "More?" }, { role: "assistant", text: "Sure" }]), []);
  // The window draws it under the chat, as small pills (pilot-panel.css), and not while Pilot is busy.
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  assert.ok(panel.includes("nodes.push(againRow());"));
  assert.match(panel, /function againRow\(\) \{\s*if \(state\.busy \|\| otherBusy \|\| cards\.size\) return null;/);
  assert.ok(panel.includes('el("div", { class: "pilot-pills pilot-pills-row", role: "group", "aria-label": "More you can ask" }'));
  const css = fs.readFileSync(core("pilot-panel.css"), "utf8");
  assert.match(css, /\.pilot-pills\.pilot-pills-row \{ flex-direction: row; flex-wrap: wrap;/);
});

test("privacy 1.4.1: an app's own answer never goes to the AI provider, not even after a key is added; a stand-in keeps the turns", async () => {
  // A built-in answer made from what the page holds (RankUp: chore titles and counts).
  const secret = "Mia has 3 chores left: Feed Biscuit, Tidy room, Piano 20 min";
  const store = Store.create({ prefix: "p141", local: memoryStorage(), session: memoryStorage() });
  // Shown with no key, kept in the chat (page to page, through storage)...
  store.saveChat([{ role: "user", text: "What's left today?" }, { role: "assistant", text: secret, local: true },
    { role: "note", kind: "more", text: "Want more? Add your AI key to ask anything." }]);
  // ...then a key is added and the next question goes to the provider, with the real adapter and a fake network.
  store.saveSettings(ANTHROPIC);
  const fetcher = fakeFetch(() => sseResponse([
    ev({ type: "message_start", message: { id: "m" } }, "message_start"),
    ev({ type: "content_block_start", index: 0, content_block: { type: "text", text: "" } }, "content_block_start"),
    ev({ type: "content_block_delta", index: 0, delta: { type: "text_delta", text: "Sure." } }, "content_block_delta"),
    ev({ type: "message_delta", delta: { stop_reason: "end_turn" } }, "message_delta"),
    ev({ type: "message_stop" }, "message_stop"),
  ]));
  const engine = Engine.create({ store, providers: Providers, host: { fetch: fetcher }, config: { appName: "Test App", tools: [] } });
  await engine.send("Can you help me plan the evening?");
  assert.equal(fetcher.calls.length, 1);
  const wire = JSON.stringify(fetcher.calls[0].body);
  for (const bit of ["Mia", "Biscuit", "Tidy room", "Piano", "3 chores"]) assert.ok(!wire.includes(bit), bit);
  assert.ok(wire.includes(Engine.LOCAL_STANDIN), "the stand-in keeps the turns in order");
  assert.ok(wire.includes("What's left today?") && wire.includes("Can you help me plan the evening?"));
  // It is still on screen, unchanged.
  assert.equal(engine.messages.find((m) => m.local).text, secret);
  assert.equal(store.loadChat().find((m) => m.local).text, secret);
  // AI answers are sent as before.
  const { engine: e2, sent } = engineWith({ replies: [{ text: "One" }, { text: "Two" }] });
  await e2.send("First");
  await e2.send("Second");
  assert.ok(sent[1].messages.some((m) => m.role === "assistant" && m.text === "One"));
});

test("robot 1.4.1: the pictures can come in the size they are drawn (crisp on phones); without them, as before", () => {
  const node = (tag) => ({ tag, attrs: {}, kids: [], setAttribute(k, v) { this.attrs[k] = v; }, appendChild(c) { this.kids.push(c); },
    set className(v) { this.attrs.class = v; } });
  const doc = { createElementNS: (ns, tag) => node(tag), createElement: node };
  const img = Mascot.robot(doc, { small: "/r128.webp", large: "/r256.webp", set: "/r112.webp 112w, /r168.webp 168w, /r176.webp 176w" });
  assert.equal(img.attrs.srcset, "/r128.webp 128w, /r256.webp 256w, /r112.webp 112w, /r168.webp 168w, /r176.webp 176w");
  assert.equal(img.attrs.sizes, Mascot.SIZES);
  assert.equal(Mascot.SIZES, "(max-width: 720px) 56px, 88px");
  assert.equal(Mascot.robot(doc, { small: "/r128.webp", large: "/r256.webp" }).attrs.srcset, "/r128.webp 128w, /r256.webp 256w");
  // The files are there, at the sizes named: a phone's 56 px at 2x and 3x, a computer's 88 px at 2x.
  for (const [f, w] of [["pilot-robot-112.webp", 112], ["pilot-robot-168.webp", 168], ["pilot-robot-176.webp", 176],
    ["pilot-rest-body-112.webp", 112], ["pilot-rest-head-168.webp", 168], ["pilot-rest-eyes-112.webp", 112]]) {
    const b = fs.readFileSync(core(f));
    assert.equal(b.toString("latin1", 8, 12), "WEBP", f);
    // VP8X canvas width (24-bit, minus one) at byte 24.
    assert.equal(b.readUIntLE(24, 3) + 1, w, f);
  }
  // The resting robot's layers: the loader hands the actor {src, srcset, sizes} when the page names the sizes;
  // the actor (shared with Pilot for Mac, which passes addresses) takes either.
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  assert.ok(loader.includes("art: M().restArt(assets),"));
  assert.ok(loader.includes("set: assets.robotSet"));
  assert.deepEqual(Mascot.restArt({ restBody: "/b.webp", restHead: "/h.webp", restEyes: "/e.webp" }), { body: "/b.webp", head: "/h.webp", eyes: "/e.webp" });
  assert.deepEqual(Mascot.restArt({ restBody: "/b.webp", restBodySet: "/b112.webp 112w, /b168.webp 168w", restHead: "/h.webp", restEyes: "/e.webp" }).body,
    { src: "/b.webp", srcset: "/b112.webp 112w, /b168.webp 168w, /b.webp 192w", sizes: Mascot.SIZES });
  const actor = fs.readFileSync(core("pilot-actor.js"), "utf8");
  assert.ok(actor.includes('const s = src && typeof src === "object" ? src : { src };'));
});

test("welcome 1.4.1: the \"Want more?\" line shows once per chat, under the first answer of the app's own", () => {
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  const answerFn = panel.slice(panel.indexOf("async function answerWith"), panel.indexOf("// A suggestion or pill that needs no AI"));
  assert.ok(answerFn.includes('const offered = state.messages.some((m) => m.role === "note" && m.kind === "more");'));
  assert.ok(answerFn.includes("if (text && more && !store.ready() && !offered) add.push("));
  // A chat kept from 1.4.0 with several such lines draws only the first.
  assert.ok(panel.includes("const n = more && offered ? null : messageNode(m);"));
});

// ---------------------------------------------------------------- core 1.4.2: raised corners, the screen chip, the handle

// A made-up phone page for placement: each item {cls, rect: [left, top, width, height], press (a link or a button),
// fixed / sticky (a bar that stays put), words (its own words fill its box), in (the index of the item it sits in)}.
// Later items are on top. own: Pilot's root, whose style gives the CSS variables and its own `bottom` (cssBottom).
function fakePage({ width = 375, height = 812, items = [], vars = {}, cssBottom, cssTop } = {}) {
  const R = (l, t, w, h) => ({ left: l, top: t, right: l + w, bottom: t + h, width: w, height: h });
  const els = [];
  for (const it of items) {
    const r = R(...it.rect);
    const e = { nodeType: 1, tagName: it.tag || (it.press ? "A" : "DIV"), cls: it.cls || "", press: Boolean(it.press),
      parentElement: it.in === undefined ? null : els[it.in],
      style: { position: it.fixed ? "fixed" : it.sticky ? "sticky" : "static", visibility: "visible", display: "block", opacity: "1" },
      getBoundingClientRect: () => r, getClientRects: () => [r],
      childNodes: it.words ? [{ nodeType: 3, nodeValue: "words", rects: [r] }] : [] };
    e.closest = (sel) => {
      if (sel === Mascot.INTERACTIVE) { for (let n = e; n; n = n.parentElement) if (n.press) return n; }
      return null;
    };
    els.push(e);
  }
  const kids = [];
  const classes = new Set();
  const own = { nodeType: 1, contains: (n) => n === own || kids.includes(n), appendChild: (n) => { kids.push(n); },
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) }, kids };
  const ownStyle = { getPropertyValue: (name) => (vars[name] === undefined ? "" : String(vars[name])),
    bottom: cssBottom === undefined ? undefined : `${cssBottom}px`, top: cssTop === undefined ? undefined : `${cssTop}px` };
  const win = { innerWidth: width, innerHeight: height,
    getComputedStyle: (el) => (el === own ? ownStyle : { ...el.style, getPropertyValue: () => "" }) };
  const inside = (r, x, y) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
  const doc = { body: { nodeType: 1 }, defaultView: win,
    querySelectorAll: (sel) => (sel === Mascot.INTERACTIVE ? els.filter((e) => e.press)
      : sel.startsWith(".") ? els.filter((e) => e.cls === sel.slice(1)) : []),
    createRange: () => ({ selectNodeContents(n) { this.n = n; }, getClientRects() { return (this.n && this.n.rects) || []; } }),
    elementsFromPoint: (x, y) => els.filter((e) => inside(e.getBoundingClientRect(), x, y)).reverse(),
    createElement: (tag) => {
      const c = new Set();
      return { tagName: tag.toUpperCase(), style: {}, attrs: {}, kids: [], on: {},
        classList: { add: (k) => c.add(k), remove: (k) => c.delete(k), contains: (k) => c.has(k), toggle: (k, on) => (on ? c.add(k) : c.delete(k)) },
        get className() { return [...c].join(" "); }, set className(v) { c.clear(); for (const k of String(v).split(" ").filter(Boolean)) c.add(k); },
        setAttribute(k, v) { this.attrs[k] = v; }, appendChild(n) { this.kids.push(n); }, addEventListener(k, fn) { this.on[k] = fn; } };
    } };
  doc.activeElement = doc.body;
  const pic = { className: "pilot-robot", cloneNode() { return { className: "pilot-robot", src: "/robot.webp" }; } };
  const launcher = { offsetWidth: width > 720 ? 88 : 56, offsetHeight: width > 720 ? 85 : 54,
    getBoundingClientRect: () => R(width - 68, height - 66, 56, 54), querySelector: (sel) => (sel === ".pilot-robot" ? pic : null) };
  return { doc, win, own, launcher, els, avoid: [], corner: "br", shown: "br", full: true, text: true, keepIn: true };
}

// OmniArmor's signed-in phone page: a fixed tab bar (64 px, with its links) along the bottom; the app raises the
// robot's corners above it (--pilot-gap: 76px; bottom: 76px) and keeps the core's 3 px margin and 0.82 scale.
const PHONE_KEEP = { "--pilot-peek-scale": 0.82, "--pilot-keep-margin": 3, "--pilot-keep-room": 12, "--pilot-peek-show": 20 };
function tabBarPage({ width = 375, height = 812, raised = true, linksAbove = false } = {}) {
  const items = [
    { cls: "lines", rect: [16, 300, width - 46, height - 400], words: true },
    { cls: "tabbar", rect: [0, height - 64, width, 64], fixed: true },
  ];
  for (let i = 0; i < 4; i++) items.push({ rect: [8 + i * ((width - 16) / 4), height - 58, (width - 16) / 4 - 4, 52], press: true, in: 1 });
  // A row of buttons just above the tab bar, edge to edge (for the "it still moves off a link" case).
  if (linksAbove) items.push({ rect: [0, height - 130, width, 60], press: true });
  const vars = raised ? { ...PHONE_KEEP, "--pilot-gap": 76 } : { ...PHONE_KEEP, "--pilot-gap": 12 };
  return fakePage({ width, height, items, vars, cssBottom: raised ? 76 : 12 });
}

test("placement 1.4.2: a corner raised above a bottom tab bar is measured there when tucked; it no longer hides on every scrolled page", () => {
  const p = tabBarPage();
  // Scrolled, nothing but words near the corner: tucked in its bottom corner, above the tab bar (1.4.1: hidden).
  const place = Mascot.placeNow({ ...p, scrolled: true });
  assert.deepEqual(place, { corner: "br", peek: true, typing: false });
  // At the top of the page too, and with the robot's first look, it never hides for the tab bar.
  assert.equal(Mascot.placeNow(p).hide, undefined);
  assert.equal(Mascot.placeNow({ ...p, scrolled: true, full: false }).hide, undefined);
  // The tucked box: above the tab bar, never nearer the bottom than the corner's raise (76 px less the core's own
  // 9 px pull: 67 px, where OmniArmor's own CSS draws it), the core's 3 px from the side.
  const keep = { scale: 0.82, margin: 3, pull: 12 - 3 };
  const box = Mascot.peekBox("br", { width: 375, height: 812 }, { width: 56, height: 54 }, { x: 76, b: 76, t: 76 }, 20, keep);
  assert.equal(box.right, 375 - 3);
  assert.equal(box.bottom, 812 - 67);
  assert.ok(box.bottom <= 812 - 64, "never over the tab bar");
  // The phone's safe area (the root's real `bottom`, 76 + 34 px) is honoured too.
  const safe = fakePage({ ...{ width: 375, height: 812 }, vars: { ...PHONE_KEEP, "--pilot-gap": 76 }, cssBottom: 110 });
  assert.deepEqual(Mascot.edgeRoom(safe.own, safe.win.getComputedStyle(safe.own), 76, "br"), { x: 76, b: 110, t: 76 });
  // Kept for the other edge, so a bottom corner is measured the same from a top corner (no flipping).
  assert.deepEqual(Mascot.edgeRoom(safe.own, { getPropertyValue: () => "", top: "12px" }, 76, "tr"), { x: 76, b: 110, t: 12 });
  // A link right above the bar still moves it: hidden (scrolled, links under both bottom corners), as in 1.4.1.
  const busy = tabBarPage({ linksAbove: true });
  assert.equal(Mascot.placeNow({ ...busy, scrolled: true }).hide, true);
});

test("placement 1.4.2: apps that set nothing are measured exactly as before; pilot.css pulls a raised corner only by the core's own room", () => {
  const view = { width: 375, height: 812 };
  const size = { width: 56, height: 54 };
  // The core's own gap: 3 px from the edges, as in 1.4.1, with or without the new pull.
  const old = Mascot.peekBox("br", view, size, 12, 20, { scale: 0.82, margin: 3 });
  assert.deepEqual(Mascot.peekBox("br", view, size, 12, 20, { scale: 0.82, margin: 3, pull: 9 }), old);
  assert.deepEqual(Mascot.peekBox("tl", view, size, { x: 12, b: 12, t: 12 }, 20, { scale: 0.82, margin: 3, pull: 9 }), Mascot.peekBox("tl", view, size, 12, 20, { scale: 0.82, margin: 3 }));
  assert.equal(old.bottom, 812 - 3);
  // A number gap is both ways, as before; an object gives each edge its own.
  assert.deepEqual(Mascot.gapAt(16, "br"), { x: 16, y: 16 });
  assert.deepEqual(Mascot.gapAt({ x: 12, b: 76, t: 12 }, "bl"), { x: 12, y: 76 });
  assert.deepEqual(Mascot.cornerBox("br", view, size, { x: 12, b: 76, t: 12 }), { left: 307, top: 812 - 76 - 54, right: 363, bottom: 812 - 76 });
  // The same page with the core's own corner (no tab bar raise, nothing set): unchanged placement.
  const plain = phonePage();
  assert.deepEqual(Mascot.placeNow({ ...plain, scrolled: true }), { corner: "br", peek: true, typing: false });
  // pilot.css: sideways as before (--pilot-keep-d); up or down by --pilot-keep-dy, the core's own room (16 px, 12 px
  // on a phone, the same as its gap, so nothing moves for an app that sets nothing).
  const css = fs.readFileSync(core("pilot.css"), "utf8");
  assert.ok(css.includes("--pilot-keep-dy: calc(min(var(--pilot-gap), var(--pilot-keep-room)) - var(--pilot-keep-margin));"));
  assert.ok(css.includes("--pilot-keep-room: 16px;"));
  assert.match(css, /@media \(max-width: 720px\) \{\s*\.pilot-root \{ --pilot-size: 56px; --pilot-gap: 12px;[^}]*--pilot-keep-room: 12px; \}/);
  assert.ok(css.includes("transform: translate(var(--pilot-keep-d), var(--pilot-keep-dy)) scale(var(--pilot-peek-scale));"));
  assert.ok(!/translate\((?:calc\([^)]*\)\)|var\([^)]*\)), (?:calc\(-1 \* )?var\(--pilot-keep-d\)/.test(css), "no corner pulls up or down by --pilot-keep-d any more");
  assert.equal((css.match(/var\(--pilot-keep-dy\)\)\)? scale\(var\(--pilot-peek-scale\)\)/g) || []).length, 4);
  // placeNow reads the pull from the same variables.
  const mascot = fs.readFileSync(core("pilot-mascot.js"), "utf8");
  assert.ok(mascot.includes('pull: Math.min(side, num("--pilot-keep-room", side)) - margin'));
});

test("chips 1.4.2: \"Summarise this\" only once a key is set; an app can leave it out or put its own in its place", () => {
  const P = panelHelpers();
  const plain = (v) => JSON.parse(JSON.stringify(v));
  const base = "https://mtrx.example/orbyt";
  // No key: no suggestion that would only ask for one.
  assert.equal(P.screenChip(undefined, false, base), null);
  assert.deepEqual(plain(P.screenChip(undefined, true, base)),
    { label: "Summarise this", prompt: "Summarise what's on this screen in a few short points.", said: "Summarise this screen" });
  // Off.
  assert.equal(P.screenChip(false, true, base), null);
  // The app's own, needing no key: shown either way.
  assert.deepEqual(plain(P.screenChip({ label: "  What's on   this page? ", answer: "Orbyt's forms." }, false, base)),
    { label: "What's on this page?", answer: "Orbyt's forms." });
  assert.deepEqual(plain(P.screenChip({ label: "See the demo", link: "/demo" }, false, base)), { label: "See the demo", link: "https://mtrx.example/demo" });
  // A link to another site, or an empty answer: left out (as for any suggestion).
  assert.equal(P.screenChip({ label: "Elsewhere", link: "https://other.example/" }, true, base), null);
  assert.equal(P.screenChip({ label: "Nothing", answer: "  " }, true, base), null);
  // The app's own question about the screen: it needs the AI, so only with a key.
  assert.equal(P.screenChip({ label: "Explain my checks", prompt: "Explain the checks on this screen." }, false, base), null);
  assert.deepEqual(plain(P.screenChip({ label: "Explain my checks", prompt: "Explain the checks on this screen." }, true, base)),
    { label: "Explain my checks", prompt: "Explain the checks on this screen.", said: "Explain my checks" });
  // Anything else given: the default.
  assert.equal(P.screenChip({ label: "" }, false, base), null);
  assert.equal(plain(P.screenChip(true, true, base)).label, "Summarise this");
  // The window uses it: no "Summarise this" pushed after every app's suggestions any more.
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  const draw = panel.slice(panel.indexOf("function drawChips()"), panel.indexOf("function clearChat()"));
  assert.ok(draw.includes("const own = screenChip(config.screenChip, store.ready(), location.href);"));
  assert.ok(draw.includes("if (own) all.push({ label: own.label, run: () => (own.prompt ? shareScreen(own.prompt, own.said) : runChip(own)) });"));
  assert.ok(!draw.includes('all.push({ label: "Summarise this"'));
});

// CyberLogix's console with an open alert, 320x640, scrolled to 500 (core-issues.md item 4): the sticky "Needs you
// now" bar (avoided) across the top, the search box and the "Everything" tab (both avoided) along the bottom.
function consolePage() {
  const items = [
    { cls: "row", rect: [16, 60, 288, 520], words: true },
    { cls: "needs", rect: [0, 0, 320, 56], sticky: true, words: true },
    { cls: "finder", tag: "INPUT", rect: [0, 590, 200, 50], press: true },
    { cls: "f-tab", tag: "BUTTON", rect: [200, 596, 120, 44], press: true },
  ];
  const p = fakePage({ width: 320, height: 640, items, vars: { ...PHONE_KEEP, "--pilot-gap": 12 }, cssBottom: 12 });
  return { ...p, avoid: [".needs", ".finder", ".f-tab"] };
}

test("placement 1.4.2: CyberLogix's 320 px console with every corner busy: hidden, never over the search box or a tab, scrolled or not", () => {
  const p = consolePage();
  for (const scrolled of [true, false]) {
    for (const shown of ["br", "bl"]) {
      for (const full of [true, false]) {
        const place = Mascot.placeNow({ ...p, shown, scrolled, full });
        if (!full && !scrolled) continue; // mid-scroll at the top only tucks in place (pilot.js never asks that at rest)
        assert.equal(place.hide, true, JSON.stringify({ scrolled, shown, full, place }));
        assert.equal(place.corner[0], "b", "it keeps a bottom corner, so it comes back there");
      }
    }
  }
  // The handle that may show instead never covers the bar, the search box or the tab: here, at an edge between them.
  const spot = Mascot.handleSpot(p);
  assert.ok(spot, "the side edges between the bars are free");
  const sq = Mascot.handleBox(spot.side, spot.top, { width: 320, height: 640 });
  for (const e of p.els.filter((x) => x.cls !== "row")) assert.ok(!Mascot.overlaps(sq, e.getBoundingClientRect(), 0), e.cls);
});

test("handle 1.4.2: hidden at rest, a 44 px handle at a screen edge where it covers nothing pressed, no bar and nothing avoided", () => {
  assert.equal(Mascot.HANDLE, 44);
  assert.equal(Mascot.HANDLE_MS, 2000);
  const W = 375, H = 812;
  const sq = (s) => Mascot.handleBox(s.side, s.top, { width: W, height: H });
  // An empty page: the robot's own side, near the bottom (above the phone's bottom edge).
  const empty = fakePage();
  assert.deepEqual(Mascot.handleSpot(empty), { side: "r", top: H - 44 - 12 });
  assert.deepEqual(Mascot.handleSpot({ ...empty, corner: "bl" }), { side: "l", top: H - 44 - 12 });
  // Big link cards across the whole bottom half and a header with links (Orbyt's path cards): above the cards.
  const cards = fakePage({ items: [
    { cls: "head", rect: [0, 0, W, 53], sticky: true }, { rect: [12, 6, 200, 42], press: true, in: 0 },
    { rect: [0, 400, W, 200], press: true }, { rect: [0, 604, W, 208], press: true }] });
  const s1 = Mascot.handleSpot(cards);
  assert.ok(s1 && s1.top + 44 <= 400 - 4 && s1.top >= 53, JSON.stringify(s1));
  for (const e of cards.els) assert.ok(!Mascot.overlaps(sq(s1), e.getBoundingClientRect(), 0));
  // Links down the whole right edge: the left edge.
  const right = fakePage({ items: [{ rect: [W - 60, 0, 60, H], press: true }] });
  assert.equal(Mascot.handleSpot(right).side, "l");
  // Something the app asks it to avoid counts like a link.
  const avoided = fakePage({ items: [{ cls: "badge", rect: [W - 50, 600, 50, 212] }] });
  const s2 = Mascot.handleSpot({ ...avoided, avoid: [".badge"] });
  assert.ok(s2.side === "r" && s2.top + 44 <= 600 - 4, JSON.stringify(s2));
  // A fixed tab bar's empty part is no place for it either.
  const bar = tabBarPage();
  assert.ok(Mascot.handleSpot(bar).top + 44 <= H - 64, JSON.stringify(Mascot.handleSpot(bar)));
  // Words only under it (the see-through part of the square): only when no spot is free.
  const words = fakePage({ items: [{ rect: [0, 0, W, H], words: true }] });
  assert.deepEqual(Mascot.handleSpot(words), { side: "r", top: H - 44 - 12 });
  const someFree = fakePage({ items: [{ rect: [0, 200, W, H - 200], words: true }] });
  assert.ok(Mascot.handleSpot(someFree).top + 44 <= 200, JSON.stringify(Mascot.handleSpot(someFree)));
  // Truly nothing free (RankUp's gift page: full-width Pay buttons all the way down): it stays hidden.
  const pay = [];
  for (let y = 0; y < H; y += 100) pay.push({ tag: "BUTTON", rect: [0, y, W, 96], press: true });
  assert.equal(Mascot.handleSpot(fakePage({ items: pay })), null);
  // A spot is checked again: once a link comes under it, it's no longer free.
  assert.equal(Mascot.handleFree(empty, { side: "r", top: 300 }), true);
  const moved = fakePage({ items: [{ rect: [W - 100, 290, 100, 40], press: true }] });
  assert.equal(Mascot.handleFree(moved, { side: "r", top: 300 }), false);
});

test("handle 1.4.2: it shows after 2 s hidden with the page still, as a button that opens Pilot; gone when the robot is back or the page moves", (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const p = fakePage();
  let opened = 0;
  const make = (page) => Mascot.restHandle({ doc: page.doc, win: page.win, root: page.own, launcher: page.launcher, open: () => { opened += 1; },
    avoid: () => page.avoid, corner: () => "br" });
  const h = make(p);
  p.own.classList.add("pilot-away");
  h.update(true);
  t.mock.timers.tick(1900);
  assert.equal(p.own.classList.contains("pilot-handle-on"), false, "not before about 2 s");
  t.mock.timers.tick(200);
  assert.equal(p.own.classList.contains("pilot-handle-on"), true);
  const btn = p.own.kids[0];
  assert.equal(btn.tagName, "BUTTON");
  assert.equal(btn.type, "button");
  assert.equal(btn.attrs["aria-label"], "Open Pilot, your AI helper");
  assert.ok(btn.classList.contains("pilot-handle") && btn.classList.contains("pilot-handle-r"));
  assert.equal(btn.style.top, `${812 - 44 - 12}px`);
  assert.equal(btn.kids[0].className, "pilot-handle-robot", "a slice of the robot's own picture");
  btn.on.click();
  assert.equal(opened, 1);
  // Still at rest: it stays. The page scrolls or the robot comes back: gone at once.
  h.update(true);
  assert.equal(p.own.classList.contains("pilot-handle-on"), true);
  h.update(false);
  assert.equal(p.own.classList.contains("pilot-handle-on"), false);
  // Back at rest: 2 s again.
  h.update(true);
  t.mock.timers.tick(2100);
  assert.equal(p.own.classList.contains("pilot-handle-on"), true);
  // A box is being typed in when the time comes: no handle.
  const q = fakePage();
  const hq = make(q);
  q.own.classList.add("pilot-away");
  hq.update(true);
  q.doc.activeElement = { nodeType: 1, tagName: "INPUT", type: "text" };
  t.mock.timers.tick(2100);
  assert.equal(q.own.classList.contains("pilot-handle-on"), false);
  // Nowhere free: it stays hidden.
  const pay = [];
  for (let y = 0; y < 812; y += 100) pay.push({ tag: "BUTTON", rect: [0, y, 375, 96], press: true });
  const full = fakePage({ items: pay });
  const hf = make(full);
  full.own.classList.add("pilot-away");
  hf.update(true);
  t.mock.timers.tick(2100);
  assert.equal(full.own.classList.contains("pilot-handle-on"), false);
  assert.equal(full.own.kids.length, 0);
});

test("handle 1.4.2: the loader asks for it after each look (unless config.handle is false); pilot.css shows it only while the robot is hidden", () => {
  const loader = fs.readFileSync(core("pilot.js"), "utf8");
  assert.ok(loader.includes("if (handle) handle.update(Boolean(place.hide && !place.typing && !scrolling));"));
  assert.ok(loader.includes("if (config.handle !== false) handle = M().restHandle({ doc: document, win: window, root: rootEl, launcher, open, avoid, corner: () => shownCorner });"));
  const css = fs.readFileSync(core("pilot.css"), "utf8");
  assert.match(css, /\.pilot-handle \{ display: none; position: fixed; width: 44px; height: 44px;/);
  assert.ok(css.includes(".pilot-root.pilot-away.pilot-handle-on:not(.pilot-open):not(.pilot-loading) .pilot-handle { display: block;"));
  // No other rule shows it (so a page's pop-up, which hides the whole root, hides it too).
  assert.equal((css.match(/\.pilot-handle[^{]*\{[^}]*display: block/g) || []).length, 1);
  assert.ok(css.includes(".pilot-handle:focus-visible"));
});

test("typed 1.4.2: with no key, a typed question that clearly fits one of the app's own answers gets it; anything else goes on as before", () => {
  const P = panelHelpers();
  const base = "https://clx.example/console";
  // CyberLogix-like pills and page suggestions (the reviewer's example).
  const pills = P.ownPills([
    { icon: "bars", label: "Which fridges need me?", answer: "Two fridges are warm.", match: ["fridge", "warm", "ok", "Freezers"] },
    { icon: "chat", label: "Is the food still OK?", answer: "Check the times." },
    { icon: "spark", label: "See it live", link: "/demo" },
    { icon: "bolt", label: "What does it cost?", answer: "From 5 a month." },
    { icon: "key", label: "Add my AI key", action: "settings" },
  ], base);
  const chips = P.cleanChips([{ label: "What sensors do I need?", answer: "Any of these.", match: ["sensor", "probe"] },
    { label: "Explain this page", prompt: "Explain this page." }], base);
  const items = pills.concat(chips);
  const pick = (t) => { const m = P.matchTyped(t, items); return m ? m.label : null; };
  assert.equal(pick("Is my walk-in fridge OK?"), "Which fridges need me?");
  assert.equal(pick("which fridges need me"), "Which fridges need me?");
  assert.equal(pick("Is my food still ok?"), "Is the food still OK?");
  assert.equal(pick("How much does it cost?"), "What does it cost?");
  assert.equal(pick("can I see it live"), "See it live");
  assert.equal(pick("sensors?"), "What sensors do I need?", "one word, listed by the app in `match`");
  assert.equal(pick("freezers"), "Which fridges need me?");
  assert.equal(pick("fridge"), "Which fridges need me?");
  // Never a wrong match on unrelated questions.
  for (const q of ["What's the weather like?", "Write me a poem about fridges", "How do I install a fridge sensor with my wifi router?",
    "ok", "Is it ok?", "What can you do?", "Tell me a joke", "How do I reset my password?", "Who made you?",
    "Explain this page", "Add my AI key", "", "   ", "Is the fridge food ok?"]) {
    assert.equal(pick(q), null, q);
  }
  // A prompt or a built-in (they need the AI or open settings) is never what it answers with.
  assert.equal(P.matchTyped("explain this page please", chips.filter((c) => c.prompt)), null);
  // Question words don't count.
  assert.deepEqual(JSON.parse(JSON.stringify(P.wordsOf("What is my walk-in fridge's temperature?"))), ["walk", "fridge", "temperature"]);
  assert.equal(P.MATCH_LEAD, "Here's what I can tell you without an AI key:");
  // `match` is kept only on items that answer with no AI, cleaned.
  assert.deepEqual(JSON.parse(JSON.stringify(pills[0].match)), ["fridge", "warm", "ok", "freezer"]);
  assert.equal(P.ownPills([{ label: "Q", action: "Ask this", match: ["x"] }], base)[0].match, undefined);
  // The window: no key, a typed question tries this first; the answer is the app's own (local: never sent to the AI),
  // with the line saying so; a link is shown as a link to tap; config.matchTyped: false turns it off.
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  const askFn = panel.slice(panel.indexOf("async function ask(text, extra)"), panel.indexOf("function typedHere"));
  assert.ok(askFn.includes("if (!extra && typedHere(words)) return;"));
  assert.ok(askFn.indexOf("typedHere(words)") < askFn.indexOf("hold({ text: words })"));
  assert.ok(panel.includes('if (config.matchTyped === false || answering || state.busy) return false;'));
  assert.ok(panel.includes("answerWith(words, hit.link ? `[${safe}](${hit.link})` : hit.answer, MATCH_LEAD);"));
  assert.ok(panel.includes('{ role: "assistant", text: lead ? `${lead}\\n\\n${text}` : text, local: true }'));
});

test("placement 1.4.2: on a computer a link within 8 px counts; never a top corner over the header's links; it hides rather than touch one", () => {
  assert.equal(Mascot.CONTROL_PAD, 8);
  assert.equal(Mascot.TOUCH, Mascot.WEIGHT.control * 0.1);
  const W = 1024, H = 800;
  // Genesis at 1024 px: "Save my spark" ends 7 px into the tucked robot's corner, a text box at the bottom left,
  // the header's links at both top corners.
  const page = (saveRight) => fakePage({ width: W, height: H, vars: { "--pilot-peek-scale": 0.52, "--pilot-keep-margin": 8, "--pilot-keep-room": 16 }, cssBottom: 16,
    items: [
      { cls: "head", rect: [0, 0, W, 72], sticky: true }, { rect: [31, 14, 200, 44], press: true, in: 0 }, { rect: [880, 22, 120, 30], press: true, in: 0 },
      { tag: "INPUT", rect: [47, 752, 300, 46], press: true },
      { tag: "BUTTON", rect: [saveRight - 130, 752, 130, 46], press: true },
    ] });
  const near = page(977);
  // The tucked robot's box at the bottom right starts at 970: the button reaches 7 px in. Without the pad, a grid
  // point might miss that sliver; with it, it counts.
  const slice = Mascot.peekBox("br", { width: W, height: H }, { width: 88, height: 85 }, 16, 36, { scale: 0.52, margin: 8, pull: 8 });
  assert.ok(Mascot.contentIn(near.doc, slice, near.own, 4, 8).some((h) => h.kind === "control"));
  // A button 6 px short of the box counts too; words that near don't.
  const gap6 = page(964);
  assert.ok(Mascot.contentIn(gap6.doc, slice, gap6.own, 4, 8).some((h) => h.kind === "control"));
  const words = fakePage({ width: W, height: H, items: [{ rect: [900, 740, 64, 20], words: true }] });
  assert.deepEqual(Mascot.contentIn(words.doc, slice, words.own, 4, 8), []);
  // Every corner touches a link or a box: on a computer it now hides (the handle shows instead), keeping a
  // bottom corner; never a top corner over the header's links (1.4.1 tucked over the header's last link on Orbyt).
  const place = Mascot.placeNow({ ...near, shown: "br" });
  assert.equal(place.hide, true, JSON.stringify(place));
  assert.equal(place.corner[0], "b");
  // An app can keep 1.4.1's tucking (hideWhenCrowded: false), and still never in a top corner over the links.
  const kept = Mascot.placeNow({ ...near, away: false });
  assert.equal(kept.hide, undefined);
  assert.equal(kept.corner[0], "b", JSON.stringify(kept));
  // Room at the bottom right: it sits there, as before.
  const room = page(700);
  assert.deepEqual(Mascot.placeNow(room), { corner: "br", peek: false, typing: false });
  // The empty end of a header (no link there) is still a place to tuck on a computer, as before.
  const wide = phonePage({ cardAtBottom: true, width: 1366, height: 900 });
  assert.equal(Mascot.placeNow(wide).hide, undefined);
});

test("welcome 1.4.2: under the app's own answer, this page's suggestions first, then the welcome's pills, each once", () => {
  const P = panelHelpers();
  const plain = (v) => JSON.parse(JSON.stringify(v));
  const base = "https://mtrx.example/";
  const pills = P.ownPills([
    { icon: "spark", label: "Help with my idea", link: "/genesis#new" },
    { icon: "chat", label: "What is Orbyt?", answer: "Orbyt is..." },
    { icon: "bolt", label: "What apps are coming?", answer: "Three apps." },
    { icon: "key", label: "Add my AI key", action: "settings" },
  ], base);
  const chips = P.cleanChips([{ label: "Which app fits me?", answer: "One for each." }, { label: "What is Orbyt?", answer: "Dup." },
    { label: "Explain this page", prompt: "Explain it." }], base);
  const local = [{ role: "user", text: "Which app fits me?" }, { role: "assistant", text: "One for each.", local: true }];
  const got = plain(P.againItems(local, pills, chips, true));
  // The one just asked is left out; the page's own next; a pill with the same words as a suggestion shows once.
  assert.deepEqual(got.map((x) => x.label), ["What is Orbyt?", "Explain this page", "Help with my idea", "What apps are coming?", "Add my AI key"]);
  assert.equal(got[0].chip, true);
  assert.equal(got[0].answer, "Dup.", "the page's own suggestion wins");
  assert.equal(got[2].chip, undefined);
  // At most six; nothing under an AI answer.
  const many = P.cleanChips([{ label: "A b", answer: "x" }, { label: "C d", answer: "x" }, { label: "E f", answer: "x" }], base);
  const capped = plain(P.againItems(local, pills.concat(P.ownPills([{ label: "G h", answer: "x" }, { label: "I j", answer: "x" }], base)), many, true));
  assert.equal(capped.length, P.AGAIN_MOST + 1, "six, and the key pill after them");
  assert.equal(capped[capped.length - 1].label, "Add my AI key");
  assert.equal(plain(P.againItems(local, pills, many, false)).some((x) => x.label === "Add my AI key"), false);
  assert.deepEqual(plain(P.againItems([{ role: "user", text: "Hi" }, { role: "assistant", text: "Hello" }], pills, chips, true)), []);
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  assert.ok(panel.includes("const list = againItems(state.messages, pills, pageChips(),"));
  assert.ok(panel.includes("() => (p.chip ? runChip(p) : runPill(p))"));
});

test("panel 1.4.2: scrolled, the chat's top edge fades under the header instead of showing half a line", () => {
  const panel = fs.readFileSync(core("pilot-panel.js"), "utf8");
  assert.ok(panel.includes('scroller.addEventListener("scroll", () => scroller.classList.toggle("pilot-scrolled", scroller.scrollTop > 1), { passive: true });'));
  const css = fs.readFileSync(core("pilot-panel.css"), "utf8");
  assert.match(css, /\.pilot-scroll\.pilot-scrolled \{ -webkit-mask-image: linear-gradient\(to bottom, transparent 0, #000 16px\);\s*mask-image: linear-gradient/);
});
