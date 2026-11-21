// Pilot core 1.2.0 · pilot-providers.js
// The AI providers Pilot can talk to, straight from the browser with the
// user's own key. The key goes only to the provider the user chose, in the
// header that provider asks for, and never to the app's own server.
//
//   anthropic   api.anthropic.com, with its browser-access header
//   openai      api.openai.com (Chat Completions)
//   gemini      generativelanguage.googleapis.com
//   compatible  any OpenAI-compatible address the app's security policy
//               allows (the app lists them in its config, e.g. a provider
//               such as OpenRouter, or a model running on the user's own
//               computer at http://localhost)
//
// No model names are written here: models come from the provider's own list
// for the user's key, or the user types one.
//
// Every adapter speaks Pilot's own message shape (pilot-engine.js):
//   {role: "user", text}
//   {role: "assistant", text, toolCalls: [{id, name, args}], raw}
//   {role: "tool", results: [{id, name, content, isError}]}
// `raw` keeps the provider's own reply exactly, so it can be sent back
// unchanged inside the same provider's tool loop (some providers attach
// signed reasoning that must come back as it was).
//
// No DOM here. `fetch` is passed in, so Node tests can mock the providers.
(function (root) {
  "use strict";

  const VERSION = "1.2.0";
  const ANTHROPIC_VERSION = "2026-10-21";
  const DEFAULT_MAX_TOKENS = 4096; // when the provider doesn't say; fits any model
  const MOST_TOKENS = 16000; // a chat answer never needs more

  class PilotError extends Error {
    // kind: key | quota | rate | model | network | refused | tools | server | address | other
    constructor(kind, message, status) {
      super(message);
      this.kind = kind;
      this.status = status || 0;
    }
  }

  const LIST = [
    { id: "anthropic", label: "Anthropic (Claude)", keyHint: "Your Anthropic API key", keyRequired: true,
      where: "console.anthropic.com" },
    { id: "openai", label: "OpenAI", keyHint: "Your OpenAI API key", keyRequired: true, where: "platform.openai.com" },
    { id: "gemini", label: "Google Gemini", keyHint: "Your Gemini API key", keyRequired: true, where: "aistudio.google.com" },
    { id: "compatible", label: "Other (OpenAI-compatible)", keyHint: "Its API key, if it needs one", keyRequired: false,
      needsBaseUrl: true, where: "" },
  ];
  const NAMES = Object.fromEntries(LIST.map((p) => [p.id, p.label.replace(/ \(.*\)$/, "")]));

  // ---------------------------------------------------------------- helpers

  function providerName(settings) {
    if (settings && settings.provider === "compatible" && settings.baseUrl) {
      try { return new URL(settings.baseUrl).host; } catch (_) { return "your provider"; }
    }
    return NAMES[settings && settings.provider] || "your provider";
  }

  // An OpenAI-compatible address the app allows, or a PilotError saying why not.
  function checkBaseUrl(raw, allowed) {
    let url;
    try { url = new URL(raw); } catch (_) {
      return new PilotError("address", "That address doesn't look right. It should start with https:// (or http://localhost).");
    }
    const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
    if (url.protocol !== "https:" && !(local && url.protocol === "http:")) {
      return new PilotError("address", "Use an https:// address. Plain http:// works only for your own computer (localhost).");
    }
    if (url.username || url.password || url.search || url.hash) {
      return new PilotError("address", "Leave out any password, ? or # part. Put the key in the key box instead.");
    }
    const hosts = Array.isArray(allowed) ? allowed : [];
    const ok = local ? hosts.includes(url.hostname) : hosts.includes(url.host);
    if (!ok) {
      return new PilotError("address", `This site can only reach ${hosts.join(", ") || "the listed providers"} from your browser. `
        + "Pick one of those, or use one of the main providers.");
    }
    return null;
  }

  function friendlyStatus(status, body, settings, sentTools) {
    const who = providerName(settings);
    const message = (body && ((body.error && (body.error.message || body.error.type)) || body.message)) || "";
    const words = String(message).toLowerCase();
    const keyProblem = status === 401 || status === 403 || /api[_ ]?key|x-api-key|authenticat|permission_denied|unauthori/.test(words);
    if (status === 400 && sentTools && /tool|function/.test(words) && !keyProblem) {
      return new PilotError("tools", "This model can't use Pilot's actions.", status);
    }
    if (keyProblem) return new PilotError("key", `Your key was refused by ${who}. Check it in Pilot settings.`, status);
    if (/credit|billing|quota|insufficient|balance|exceeded your current/.test(words)) {
      return new PilotError("quota", `${who} says this key is out of credit or over its limit. Check your account with them.`, status);
    }
    if (status === 429) return new PilotError("rate", `${who} is getting a lot of requests from this key. Wait a moment and try again.`, status);
    if (status === 400 && /image|vision|multimodal|modalit|video input/.test(words)) {
      return new PilotError("noimages", `${who} says this model can't look at pictures or videos. Pick a model that can, or another key, in Pilot settings.`, status);
    }
    if (status === 404 || /model/.test(words) && status === 400) {
      return new PilotError("model", `${who} doesn't offer the model “${settings.model}” for this key. Pick another in Pilot settings.`, status);
    }
    if (status >= 500) return new PilotError("server", `${who} is busy or having trouble right now. Try again in a minute.`, status);
    return new PilotError("other", `${who} couldn't answer that (error ${status}). Try again, or check Pilot settings.`, status);
  }

  async function call(fetcher, url, init, settings, sentTools) {
    let res;
    try {
      res = await fetcher(url, { ...init, credentials: "omit", referrerPolicy: "no-referrer" });
    } catch (e) {
      if (e && e.name === "AbortError") throw e;
      throw new PilotError("network", `Pilot couldn't reach ${providerName(settings)}. Check you're online and try again.`);
    }
    if (!res.ok) {
      let body = null;
      try { body = await res.json(); } catch (_) { body = null; }
      throw friendlyStatus(res.status, body, settings, sentTools);
    }
    return res;
  }

  // Server-sent events: calls onEvent(name, data) for each one. Lines that
  // start with ":" are comments (some providers send them to keep alive).
  async function readEvents(res, onEvent) {
    if (!res.body || typeof res.body.getReader !== "function") {
      const all = await res.text();
      handleChunk(all + "\n\n", onEvent);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split(/\r?\n\r?\n/);
      buffer = parts.pop();
      for (const part of parts) handleChunk(part, onEvent);
    }
    buffer += decoder.decode();
    if (buffer.trim()) handleChunk(buffer, onEvent);
  }

  function handleChunk(chunk, onEvent) {
    for (const block of chunk.split(/\r?\n\r?\n/)) {
      let name = "message";
      const data = [];
      for (const line of block.split(/\r?\n/)) {
        if (!line || line.startsWith(":")) continue;
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) onEvent(name, data.join("\n"));
    }
  }

  function parseJson(text) {
    try { return JSON.parse(text); } catch (_) { return null; }
  }

  const toolText = (r) => (typeof r.content === "string" ? r.content : JSON.stringify(r.content));

  // ---------------------------------------------------------------- Anthropic

  const anthropic = {
    id: "anthropic",
    headers(s) {
      return {
        "content-type": "application/json",
        "x-api-key": s.key,
        "anthropic-version": ANTHROPIC_VERSION,
        // Anthropic's API refuses browser calls without this header. The
        // risk it names (a key visible to the page) is the point here: the
        // key is the user's own, in their own browser.
        "anthropic-dangerous-direct-browser-access": "true",
      };
    },

    async models(s, fetcher, signal) {
      const out = [];
      let after = "";
      for (let page = 0; page < 5; page += 1) {
        const res = await call(fetcher, `https://api.anthropic.com/v1/models?limit=100${after ? `&after_id=${encodeURIComponent(after)}` : ""}`,
          { method: "GET", headers: this.headers(s), signal }, s);
        const body = await res.json();
        for (const m of (body && body.data) || []) {
          if (m && typeof m.id === "string") out.push({ id: m.id, label: m.display_name || m.id, maxTokens: Number(m.max_tokens) || 0 });
        }
        if (!body || !body.has_more || !body.last_id) break;
        after = body.last_id;
      }
      return out;
    },

    toWire(messages) {
      const wire = [];
      for (const m of messages) {
        if (m.role === "user") wire.push({ role: "user", content: m.text });
        else if (m.role === "assistant") {
          if (m.raw && m.raw.provider === "anthropic" && Array.isArray(m.raw.content) && m.raw.content.length) {
            wire.push({ role: "assistant", content: m.raw.content });
          } else {
            const content = [];
            if (m.text) content.push({ type: "text", text: m.text });
            for (const c of m.toolCalls || []) content.push({ type: "tool_use", id: c.id, name: c.name, input: c.args || {} });
            if (content.length) wire.push({ role: "assistant", content });
          }
        } else if (m.role === "tool") {
          wire.push({ role: "user", content: m.results.map((r) => ({
            type: "tool_result", tool_use_id: r.id, content: toolText(r), ...(r.isError ? { is_error: true } : {}) })) });
        }
      }
      return wire;
    },

    async chat(s, req, fetcher) {
      const body = {
        model: s.model,
        max_tokens: Math.min(s.maxTokens || DEFAULT_MAX_TOKENS, MOST_TOKENS),
        stream: true,
        system: req.system,
        messages: this.toWire(req.messages),
      };
      if (req.tools && req.tools.length) {
        body.tools = req.tools.map((t) => ({ name: t.name, description: t.description, input_schema: t.parameters }));
      }
      const res = await call(fetcher, "https://api.anthropic.com/v1/messages",
        { method: "POST", headers: this.headers(s), body: JSON.stringify(body), signal: req.signal }, s, Boolean(body.tools));
      const blocks = [];
      const partial = {};
      let stop = "";
      let text = "";
      let failure = null;
      await readEvents(res, (name, data) => {
        const ev = parseJson(data);
        if (!ev) return;
        if (ev.type === "content_block_start" && ev.content_block) {
          blocks[ev.index] = { ...ev.content_block };
          if (ev.content_block.type === "tool_use") partial[ev.index] = "";
        } else if (ev.type === "content_block_delta" && blocks[ev.index]) {
          const b = blocks[ev.index];
          const d = ev.delta || {};
          if (d.type === "text_delta") {
            b.text = (b.text || "") + d.text;
            text += d.text;
            if (req.onText) req.onText(text);
          } else if (d.type === "input_json_delta") partial[ev.index] += d.partial_json || "";
          else if (d.type === "thinking_delta") b.thinking = (b.thinking || "") + (d.thinking || "");
          else if (d.type === "signature_delta") b.signature = d.signature;
        } else if (ev.type === "content_block_stop" && blocks[ev.index] && blocks[ev.index].type === "tool_use") {
          const args = parseJson(partial[ev.index] || "{}");
          blocks[ev.index].input = args && typeof args === "object" ? args : {};
        } else if (ev.type === "message_delta" && ev.delta) {
          stop = ev.delta.stop_reason || stop;
        } else if (ev.type === "error") {
          failure = friendlyStatus(ev.error && ev.error.type === "overloaded_error" ? 529 : 500, ev, s);
        }
      });
      if (failure) throw failure;
      const content = blocks.filter(Boolean).map((b) => {
        // Drop the streaming-only leftovers; keep everything else as sent.
        const copy = { ...b };
        if (copy.type === "text" && typeof copy.text !== "string") copy.text = "";
        if (copy.type === "text" && copy.citations === null) delete copy.citations;
        return copy;
      });
      const toolCalls = content.filter((b) => b.type === "tool_use").map((b) => ({ id: b.id, name: b.name, args: b.input || {} }));
      return {
        text, toolCalls,
        stop: stop === "refusal" ? "refused" : stop === "max_tokens" ? "length" : stop === "tool_use" ? "tools" : "done",
        raw: { provider: "anthropic", content },
      };
    },
  };

  // ---------------------------------------------------------------- OpenAI and OpenAI-compatible

  // Words that mark a model that doesn't chat (pictures, speech, search
  // indexes). Not model names: what the model is for.
  const NOT_CHAT = /embed|moderation|tts|transcri|audio|realtime|image|speech|video|search|similarity/i;

  function openaiLike(id, base) {
    return {
      id,
      base(s) { return id === "openai" ? "https://api.openai.com/v1" : s.baseUrl; },
      headers(s) {
        const h = { "content-type": "application/json" };
        if (s.key) h.authorization = `Bearer ${s.key}`;
        return h;
      },
      async models(s, fetcher, signal) {
        const res = await call(fetcher, `${this.base(s)}/models`, { method: "GET", headers: this.headers(s), signal }, s);
        const body = await res.json();
        const list = Array.isArray(body && body.data) ? body.data : Array.isArray(body && body.models) ? body.models : [];
        return list.filter((m) => m && typeof (m.id || m.name) === "string")
          .map((m) => ({ id: m.id || m.name, label: m.name && m.id ? m.name : (m.id || m.name),
            maxTokens: 0 }))
          .filter((m) => id === "compatible" || !NOT_CHAT.test(m.id))
          .sort((a, b) => a.id.localeCompare(b.id));
      },
      toWire(system, messages) {
        const wire = [{ role: "system", content: system }];
        for (const m of messages) {
          if (m.role === "user") wire.push({ role: "user", content: m.text });
          else if (m.role === "assistant") {
            const msg = { role: "assistant", content: m.text || null };
            if (m.toolCalls && m.toolCalls.length) {
              msg.tool_calls = m.toolCalls.map((c) => ({ id: c.id, type: "function",
                function: { name: c.name, arguments: JSON.stringify(c.args || {}) } }));
            }
            if (msg.content !== null || msg.tool_calls) wire.push(msg);
          } else if (m.role === "tool") {
            for (const r of m.results) wire.push({ role: "tool", tool_call_id: r.id, content: toolText(r) });
          }
        }
        return wire;
      },
      async chat(s, req, fetcher) {
        const body = { model: s.model, stream: true, messages: this.toWire(req.system, req.messages) };
        if (req.tools && req.tools.length) {
          body.tools = req.tools.map((t) => ({ type: "function",
            function: { name: t.name, description: t.description, parameters: t.parameters } }));
        }
        const res = await call(fetcher, `${this.base(s)}/chat/completions`,
          { method: "POST", headers: this.headers(s), body: JSON.stringify(body), signal: req.signal }, s, Boolean(body.tools));
        let text = "";
        let stop = "";
        let failure = null;
        const calls = [];
        await readEvents(res, (_name, data) => {
          if (data.trim() === "[DONE]") return;
          const ev = parseJson(data);
          if (!ev) return;
          if (ev.error) { failure = friendlyStatus(Number(ev.error.code) || 500, ev, s, Boolean(body.tools)); return; }
          const choice = ev.choices && ev.choices[0];
          if (!choice) return;
          const d = choice.delta || {};
          if (typeof d.content === "string" && d.content) {
            text += d.content;
            if (req.onText) req.onText(text);
          }
          for (const t of d.tool_calls || []) {
            const i = Number.isInteger(t.index) ? t.index : calls.length;
            calls[i] = calls[i] || { id: "", name: "", args: "" };
            if (t.id) calls[i].id = t.id;
            if (t.function && t.function.name) calls[i].name += t.function.name;
            if (t.function && t.function.arguments) calls[i].args += t.function.arguments;
          }
          if (choice.finish_reason) stop = choice.finish_reason;
        });
        if (failure) throw failure;
        const toolCalls = calls.filter((c) => c && c.name).map((c, i) => {
          const args = parseJson(c.args || "{}");
          return { id: c.id || `call_${i}`, name: c.name, args: args && typeof args === "object" ? args : {} };
        });
        return {
          text, toolCalls,
          stop: stop === "content_filter" ? "refused" : stop === "length" ? "length" : toolCalls.length ? "tools" : "done",
          raw: { provider: id },
        };
      },
    };
  }

  const openai = openaiLike("openai");
  const compatible = openaiLike("compatible");

  // ---------------------------------------------------------------- Google Gemini

  const gemini = {
    id: "gemini",
    base: "https://generativelanguage.googleapis.com/v1beta",
    headers(s) { return { "content-type": "application/json", "x-goog-api-key": s.key }; },

    async models(s, fetcher, signal) {
      const out = [];
      let token = "";
      for (let page = 0; page < 5; page += 1) {
        const res = await call(fetcher, `${this.base}/models?pageSize=1000${token ? `&pageToken=${encodeURIComponent(token)}` : ""}`,
          { method: "GET", headers: this.headers(s), signal }, s);
        const body = await res.json();
        for (const m of (body && body.models) || []) {
          const methods = Array.isArray(m.supportedGenerationMethods) ? m.supportedGenerationMethods : [];
          if (typeof m.name !== "string" || !methods.includes("generateContent")) continue;
          if (NOT_CHAT.test(m.name)) continue;
          out.push({ id: m.name.replace(/^models\//, ""), label: m.displayName || m.name, maxTokens: Number(m.outputTokenLimit) || 0 });
        }
        if (!body || !body.nextPageToken) break;
        token = body.nextPageToken;
      }
      return out;
    },

    toWire(messages) {
      const contents = [];
      const names = {};
      for (const m of messages) {
        if (m.role === "user") contents.push({ role: "user", parts: [{ text: m.text }] });
        else if (m.role === "assistant") {
          for (const c of m.toolCalls || []) names[c.id] = c.name;
          if (m.raw && m.raw.provider === "gemini" && Array.isArray(m.raw.parts) && m.raw.parts.length) {
            contents.push({ role: "model", parts: m.raw.parts });
          } else {
            const parts = [];
            if (m.text) parts.push({ text: m.text });
            for (const c of m.toolCalls || []) parts.push({ functionCall: { name: c.name, args: c.args || {} } });
            if (parts.length) contents.push({ role: "model", parts });
          }
        } else if (m.role === "tool") {
          contents.push({ role: "user", parts: m.results.map((r) => {
            const answer = { name: r.name || names[r.id] || "tool", response: r.isError ? { error: toolText(r) } : { result: toolText(r) } };
            if (r.id && !r.id.startsWith("gemini-")) answer.id = r.id;
            return { functionResponse: answer };
          }) });
        }
      }
      return contents;
    },

    async chat(s, req, fetcher) {
      const body = { systemInstruction: { parts: [{ text: req.system }] }, contents: this.toWire(req.messages) };
      if (req.tools && req.tools.length) {
        body.tools = [{ functionDeclarations: req.tools.map((t) => ({ name: t.name, description: t.description, parameters: t.parameters })) }];
      }
      const url = `${this.base}/models/${encodeURIComponent(s.model)}:streamGenerateContent?alt=sse`;
      const res = await call(fetcher, url, { method: "POST", headers: this.headers(s), body: JSON.stringify(body), signal: req.signal },
        s, Boolean(body.tools));
      let text = "";
      let stop = "";
      let failure = null;
      const parts = [];
      const toolCalls = [];
      await readEvents(res, (_name, data) => {
        const ev = parseJson(data);
        if (!ev) return;
        if (ev.error) { failure = friendlyStatus(Number(ev.error.code) || 500, ev, s, Boolean(body.tools)); return; }
        if (ev.promptFeedback && ev.promptFeedback.blockReason) stop = "refused";
        const cand = ev.candidates && ev.candidates[0];
        if (!cand) return;
        for (const p of (cand.content && cand.content.parts) || []) {
          parts.push(p);
          if (typeof p.text === "string" && !p.thought) {
            text += p.text;
            if (req.onText) req.onText(text);
          }
          if (p.functionCall && typeof p.functionCall.name === "string") {
            toolCalls.push({ id: p.functionCall.id || `gemini-${toolCalls.length}`, name: p.functionCall.name,
              args: p.functionCall.args && typeof p.functionCall.args === "object" ? p.functionCall.args : {} });
          }
        }
        if (cand.finishReason) {
          const r = cand.finishReason;
          stop = /SAFETY|BLOCKLIST|PROHIBITED|SPII|RECITATION/.test(r) ? "refused" : r === "MAX_TOKENS" ? "length" : stop || "done";
        }
      });
      if (failure) throw failure;
      return { text, toolCalls, stop: stop === "refused" ? "refused" : toolCalls.length ? "tools" : stop || "done",
        raw: { provider: "gemini", parts } };
    },
  };

  const ADAPTERS = { anthropic, openai, gemini, compatible };

  function adapter(settings) {
    const a = ADAPTERS[settings && settings.provider];
    if (!a) throw new PilotError("key", "Choose an AI provider in Pilot settings first.");
    return a;
  }

  // The models this key may use: [{id, label, maxTokens}].
  async function listModels(settings, options) {
    const o = options || {};
    if (settings.provider === "compatible") {
      const bad = checkBaseUrl(settings.baseUrl, o.compatibleHosts);
      if (bad) throw bad;
    }
    return adapter(settings).models(settings, o.fetch || root.fetch.bind(root), o.signal);
  }

  // One turn: streams the words to req.onText, returns
  // {text, toolCalls, stop: "done" | "tools" | "length" | "refused", raw}.
  async function chat(settings, req, options) {
    const o = options || {};
    if (settings.provider === "compatible") {
      const bad = checkBaseUrl(settings.baseUrl, o.compatibleHosts);
      if (bad) throw bad;
    }
    return adapter(settings).chat(settings, req, o.fetch || root.fetch.bind(root));
  }

  const api = { VERSION, LIST, NAMES, PilotError, providerName, checkBaseUrl, listModels, chat, readEvents, adapters: ADAPTERS };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PilotProviders = api;
})(typeof window !== "undefined" ? window : globalThis);
