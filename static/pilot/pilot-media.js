// Pilot core 1.0.1 · pilot-media.js
// Making pictures and videos with the user's own image or video key, from
// the browser. Only offered once the user has set such a key in Pilot
// settings; without one, Pilot has no image or video abilities at all.
//
//   images   OpenAI (Images API), Google Gemini (image models), or an
//            OpenAI-compatible address (its /images/generations)
//   videos   OpenAI (Videos API), Google Gemini (video models, a long-running
//            job), or an OpenAI-compatible address with the same Videos API
//
// Anthropic makes neither, so it isn't offered here. Nothing is proxied
// through the app's server: a provider that won't answer a browser simply
// can't be used, and Pilot says so.
//
// Pictures come back as data (base64) and are shown as data: addresses;
// videos are downloaded with the key and shown from a blob: address. No DOM
// here: the panel shows them.
(function (root) {
  "use strict";

  const VERSION = "1.0.1";
  const PROMPT_MOST = 1000;
  const E = () => root.PilotProviders;

  function error(kind, message) { return new (E().PilotError)(kind, message); }

  function base(p) {
    if (p.provider === "openai") return "https://api.openai.com/v1";
    if (p.provider === "gemini") return "https://generativelanguage.googleapis.com/v1beta";
    return p.baseUrl;
  }

  function headers(p) {
    if (p.provider === "gemini") return { "content-type": "application/json", "x-goog-api-key": p.key };
    const h = { "content-type": "application/json" };
    if (p.key) h.authorization = `Bearer ${p.key}`;
    return h;
  }

  async function call(p, fetcher, url, init, options) {
    if (p.provider === "compatible") {
      const bad = E().checkBaseUrl(p.baseUrl, options && options.compatibleHosts);
      if (bad) throw bad;
    }
    let res;
    try {
      res = await fetcher(url, { ...init, credentials: "omit", referrerPolicy: "no-referrer" });
    } catch (e) {
      if (e && e.name === "AbortError") throw e;
      throw error("network", `Pilot couldn't reach ${E().providerName(p)}. Check you're online; if it keeps happening, it may not accept calls from a browser.`);
    }
    if (!res.ok) {
      let body = null;
      try { body = await res.json(); } catch (_) { body = null; }
      const words = String((body && body.error && body.error.message) || "").toLowerCase();
      if (res.status === 401 || res.status === 403 || /api[_ ]?key/.test(words)) {
        throw error("key", `Your key was refused by ${E().providerName(p)}. Check it in Pilot settings.`);
      }
      if (/safety|policy|blocked|moderation/.test(words)) throw error("refused", `${E().providerName(p)} wouldn't make that one. Try describing it another way.`);
      if (/credit|billing|quota|balance/.test(words)) throw error("quota", `${E().providerName(p)} says this key is out of credit or over its limit.`);
      if (res.status === 429) throw error("rate", `${E().providerName(p)} is getting a lot of requests from this key. Wait a moment.`);
      if (res.status === 404) throw error("model", `${E().providerName(p)} doesn't offer “${p.model}” for this key. Pick another in Pilot settings.`);
      throw error("other", `${E().providerName(p)} couldn't do that (error ${res.status}).`);
    }
    return res;
  }

  const prompt = (text) => String(text || "").replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, PROMPT_MOST);

  // ---------------------------------------------------------------- models

  // The models this image or video key may use: [{id, label, method}].
  async function listModels(kind, p, options) {
    const o = options || {};
    const fetcher = o.fetch || root.fetch.bind(root);
    if (p.provider === "gemini") {
      const res = await call(p, fetcher, `${base(p)}/models?pageSize=1000`, { method: "GET", headers: headers(p), signal: o.signal }, o);
      const body = await res.json();
      const out = [];
      for (const m of (body && body.models) || []) {
        const methods = Array.isArray(m.supportedGenerationMethods) ? m.supportedGenerationMethods : [];
        const id = String(m.name || "").replace(/^models\//, "");
        if (!id) continue;
        // Video models run as a long job; picture models either "predict"
        // or answer generateContent with a picture.
        if (kind === "video" && methods.includes("predictLongRunning")) out.push({ id, label: m.displayName || id, method: "" });
        if (kind === "image" && (methods.includes("predict") || /image/i.test(id + (m.displayName || "")))) {
          out.push({ id, label: m.displayName || id, method: methods.includes("predict") ? "predict" : "" });
        }
      }
      return out;
    }
    const res = await call(p, fetcher, `${base(p)}/models`, { method: "GET", headers: headers(p), signal: o.signal }, o);
    const body = await res.json();
    const list = Array.isArray(body && body.data) ? body.data : [];
    // Words for what a model makes, not model names.
    const word = kind === "image" ? /image/i : /video/i;
    const picked = list.map((m) => String(m && m.id || "")).filter((id) => id && (p.provider === "compatible" || word.test(id)));
    return picked.sort().map((id) => ({ id, label: id, method: "" }));
  }

  // ---------------------------------------------------------------- pictures

  // One picture for `text`: {mime, data (base64)} or {url} when the provider
  // only gave a link.
  async function image(p, text, options) {
    const o = options || {};
    const fetcher = o.fetch || root.fetch.bind(root);
    const words = prompt(text);
    if (!words) throw error("other", "Say what the picture should show.");
    if (p.provider === "gemini") {
      if (p.method === "predict") {
        const res = await call(p, fetcher, `${base(p)}/models/${encodeURIComponent(p.model)}:predict`, { method: "POST", headers: headers(p),
          body: JSON.stringify({ instances: [{ prompt: words }], parameters: { sampleCount: 1 } }), signal: o.signal }, o);
        const body = await res.json();
        const pic = body && body.predictions && body.predictions[0];
        if (pic && pic.bytesBase64Encoded) return { mime: pic.mimeType || "image/png", data: pic.bytesBase64Encoded };
        throw error("refused", "The provider didn't send a picture back. Try describing it another way.");
      }
      const res = await call(p, fetcher, `${base(p)}/models/${encodeURIComponent(p.model)}:generateContent`, { method: "POST", headers: headers(p),
        body: JSON.stringify({ contents: [{ role: "user", parts: [{ text: words }] }],
          generationConfig: { responseModalities: ["TEXT", "IMAGE"] } }), signal: o.signal }, o);
      const body = await res.json();
      const parts = (body && body.candidates && body.candidates[0] && body.candidates[0].content && body.candidates[0].content.parts) || [];
      const pic = parts.find((x) => x.inlineData && x.inlineData.data);
      if (pic) return { mime: pic.inlineData.mimeType || "image/png", data: pic.inlineData.data };
      throw error("refused", "The provider didn't send a picture back. Try describing it another way.");
    }
    const res = await call(p, fetcher, `${base(p)}/images/generations`, { method: "POST", headers: headers(p),
      body: JSON.stringify({ model: p.model, prompt: words, n: 1 }), signal: o.signal }, o);
    const body = await res.json();
    const pic = body && Array.isArray(body.data) && body.data[0];
    if (pic && pic.b64_json) return { mime: "image/png", data: pic.b64_json };
    if (pic && typeof pic.url === "string" && /^https:\/\//.test(pic.url)) return { url: pic.url };
    throw error("refused", "The provider didn't send a picture back. Try describing it another way.");
  }

  // ---------------------------------------------------------------- videos

  // Start a video: returns {id} to check on.
  async function startVideo(p, text, options) {
    const o = options || {};
    const fetcher = o.fetch || root.fetch.bind(root);
    const words = prompt(text);
    if (!words) throw error("other", "Say what the video should show.");
    if (p.provider === "gemini") {
      const res = await call(p, fetcher, `${base(p)}/models/${encodeURIComponent(p.model)}:predictLongRunning`, { method: "POST",
        headers: headers(p), body: JSON.stringify({ instances: [{ prompt: words }] }), signal: o.signal }, o);
      const body = await res.json();
      if (!body || typeof body.name !== "string") throw error("other", "The provider didn't start the video.");
      return { id: body.name };
    }
    const res = await call(p, fetcher, `${base(p)}/videos`, { method: "POST", headers: headers(p),
      body: JSON.stringify({ model: p.model, prompt: words }), signal: o.signal }, o);
    const body = await res.json();
    if (!body || typeof body.id !== "string") throw error("other", "The provider didn't start the video.");
    return { id: body.id };
  }

  // How a video is getting on: {state: "working" | "done" | "failed",
  // progress (0-100 or null), message}.
  async function checkVideo(p, id, options) {
    const o = options || {};
    const fetcher = o.fetch || root.fetch.bind(root);
    if (p.provider === "gemini") {
      if (!/^[A-Za-z0-9/_.-]{1,300}$/.test(id)) throw error("other", "That video job isn't valid.");
      const res = await call(p, fetcher, `${base(p)}/${id}`, { method: "GET", headers: headers(p), signal: o.signal }, o);
      const body = await res.json();
      if (!body || !body.done) return { state: "working", progress: null };
      if (body.error) return { state: "failed", message: "The provider couldn't make that video." };
      const samples = body.response && body.response.generateVideoResponse && body.response.generateVideoResponse.generatedSamples;
      const uri = samples && samples[0] && samples[0].video && samples[0].video.uri;
      return uri ? { state: "done", uri } : { state: "failed", message: "The provider didn't send a video back." };
    }
    if (!/^[A-Za-z0-9_-]{1,200}$/.test(id)) throw error("other", "That video job isn't valid.");
    const res = await call(p, fetcher, `${base(p)}/videos/${id}`, { method: "GET", headers: headers(p), signal: o.signal }, o);
    const body = await res.json();
    const status = body && body.status;
    if (status === "completed") return { state: "done" };
    if (status === "failed" || status === "cancelled") return { state: "failed", message: "The provider couldn't make that video." };
    const progress = Number(body && body.progress);
    return { state: "working", progress: Number.isFinite(progress) ? Math.max(0, Math.min(100, progress)) : null };
  }

  // The finished video, as a Blob.
  async function fetchVideo(p, id, done, options) {
    const o = options || {};
    const fetcher = o.fetch || root.fetch.bind(root);
    let url;
    if (p.provider === "gemini") {
      url = String(done && done.uri || "");
      if (!url.startsWith(`${base(p)}/`) && !url.startsWith("https://generativelanguage.googleapis.com/")) {
        throw error("other", "The video is somewhere this site can't fetch from. Open it with the provider instead.");
      }
    } else {
      url = `${base(p)}/videos/${id}/content`;
    }
    const res = await call(p, fetcher, url, { method: "GET", headers: headers(p), signal: o.signal }, o);
    return res.blob();
  }

  const api = { VERSION, listModels, image, startVideo, checkVideo, fetchVideo };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PilotMedia = api;
})(typeof window !== "undefined" ? window : globalThis);
