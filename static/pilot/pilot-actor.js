// Pilot core 1.4.3 · pilot-actor.js
// Plays the robot's moves on a page (pilot-motion.js makes the plans).
//
// The resting robot is the owner's design picture in three layers that stack
// exactly into it: the body, the head (halo, headphones, visor) and the eyes.
// So the head can bob, tilt, nod and look toward the mouse, and the eyes can
// blink, without a single new drawing. The pose pictures (standing, wave,
// walk1 and the rest) are shown instead while the robot moves, cross-faded
// in with a little squash so it reads as standing up.
//
// Everything moves with the Web Animations API on transforms and opacity,
// which the browser runs on the compositor: no script runs per frame. When
// the robot rests nothing animates at all, apart from a blink (170 ms, every
// few seconds) and one slow breath every half a minute or so, from timers.
//
// live() is the web robot's own wandering (near its corner, toned down);
// the Mac app drives the actor from its main process instead.
(function (root) {
  "use strict";

  const VERSION = "1.4.3";
  const M = () => root.PilotMotion;

  function node(doc, tag, cls, attrs) {
    const n = doc.createElement(tag);
    if (cls) n.className = cls;
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
    return n;
  }

  // src: a picture's address, or (core 1.4.1) {src, srcset, sizes}, so the
  // web robot's layers can come in the size they are drawn at (crisp on
  // phones). The Mac passes addresses, as before.
  function picture(doc, cls, src) {
    const img = node(doc, "img", cls, { alt: "", draggable: "false", decoding: "async" });
    const s = src && typeof src === "object" ? src : { src };
    if (typeof s.srcset === "string" && s.srcset) {
      img.setAttribute("srcset", s.srcset);
      if (typeof s.sizes === "string" && s.sizes) img.setAttribute("sizes", s.sizes);
    }
    if (s.src) img.setAttribute("src", s.src);
    return img;
  }

  // ------------------------------------------------------------ props
  // The sports props, drawn as small SVGs (attributes only, no style
  // attributes, so a strict CSP is fine): the goal, nets, racket and court in
  // the robot's glowing blue and violet; the balls like the ones drawn into
  // the owner's throw, kick and tennis pictures, so the hand-over is seamless.
  const NS = "http://www.w3.org/2000/svg";
  let made = 0;
  function svg(doc, tag, attrs, kids) {
    const n = doc.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
    for (const kid of kids || []) n.appendChild(kid);
    return n;
  }
  function gradient(doc, id, stops, attrs) {
    return svg(doc, "linearGradient", { id, x1: 0, y1: 0, x2: 1, y2: 1, ...(attrs || {}) },
      stops.map(([o, c]) => svg(doc, "stop", { offset: o, "stop-color": c })));
  }
  const PROPS = {
    football(doc, n) {
      // Brown leather with white laces, like the one in the throw picture.
      return svg(doc, "svg", { viewBox: "0 0 32 20" }, [
        svg(doc, "defs", {}, [gradient(doc, `pf${n}`, [[0, "#b45f2a"], [0.55, "#7c3a16"], [1, "#4a210c"]])]),
        svg(doc, "ellipse", { cx: 16, cy: 10, rx: 15, ry: 8.6, fill: `url(#pf${n})`, stroke: "#3b1a08", "stroke-width": 1 }),
        svg(doc, "path", { d: "M4.5 7.5c1 1.8 1 3.2 0 5M27.5 7.5c-1 1.8-1 3.2 0 5", fill: "none", stroke: "#f8fafc", "stroke-width": 1.2, "stroke-linecap": "round" }),
        svg(doc, "path", { d: "M11 10h10M13 7.8v4.4M16 7.8v4.4M19 7.8v4.4", stroke: "#f8fafc", "stroke-width": 1.3, "stroke-linecap": "round" }),
      ]);
    },
    soccer(doc) {
      // A classic black-and-white ball, like the one in the kick picture.
      return svg(doc, "svg", { viewBox: "0 0 24 24" }, [
        svg(doc, "circle", { cx: 12, cy: 12, r: 10.6, fill: "#f8fafc", stroke: "#1e293b", "stroke-width": 1.2 }),
        svg(doc, "path", { d: "M12 7.4l3.6 2.6-1.4 4.2H9.8L8.4 10Z", fill: "#111827" }),
        svg(doc, "path", { d: "M12 1.6v5.8M15.6 10l5.4-1.8M14.2 14.2l3.4 4.6M9.8 14.2l-3.4 4.6M8.4 10 3 8.2", stroke: "#334155", "stroke-width": 1.2 }),
        svg(doc, "path", { d: "M20.5 6.5l1.4 3.8-2 1.4M3.5 6.5 2.1 10.3l2 1.4M8 21.6l4 .9 4-.9", fill: "#111827", stroke: "#111827", "stroke-width": 1 }),
      ]);
    },
    goal(doc, n) {
      return svg(doc, "svg", { viewBox: "0 0 60 80" }, [
        svg(doc, "defs", {}, [gradient(doc, `pg${n}`, [[0, "#c7d2fe"], [1, "#818cf8"]], { x2: 0, y2: 1 })]),
        svg(doc, "path", { d: "M4 78V6h30l22 14v58", fill: "none", stroke: `url(#pg${n})`, "stroke-width": 4, "stroke-linejoin": "round", "stroke-linecap": "round" }),
        svg(doc, "path", { d: "M34 6v72", stroke: "#a5b4fc", "stroke-width": 2, opacity: 0.7 }),
      ]);
    },
    net(doc) {
      const d = [];
      for (let y = 12; y < 78; y += 8) d.push(`M6 ${y}H54`);
      for (let x = 10; x < 56; x += 7) d.push(`M${x} 8V78`);
      return svg(doc, "svg", { viewBox: "0 0 60 80" }, [
        svg(doc, "path", { d: d.join(""), stroke: "#93c5fd", "stroke-width": 0.9, opacity: 0.55, fill: "none" }),
      ]);
    },
    tennisball(doc) {
      return svg(doc, "svg", { viewBox: "0 0 12 12" }, [
        svg(doc, "circle", { cx: 6, cy: 6, r: 5.2, fill: "#d9f046", stroke: "#a3c614", "stroke-width": 0.8 }),
        svg(doc, "path", { d: "M2 3.2c2.4 1.6 2.4 4 0 5.6M10 3.2c-2.4 1.6-2.4 4 0 5.6", fill: "none", stroke: "#f8fafc", "stroke-width": 0.8 }),
      ]);
    },
    tennisnet(doc) {
      const d = [];
      for (let y = 6; y < 48; y += 6) d.push(`M2 ${y}H10`);
      for (let x = 3; x < 10; x += 3) d.push(`M${x} 4V48`);
      return svg(doc, "svg", { viewBox: "0 0 12 50" }, [
        svg(doc, "path", { d: d.join(""), stroke: "#93c5fd", "stroke-width": 0.8, opacity: 0.6 }),
        svg(doc, "path", { d: "M1.5 50V3h9v47", fill: "none", stroke: "#c7d2fe", "stroke-width": 2, "stroke-linecap": "round" }),
      ]);
    },
    court(doc, n) {
      return svg(doc, "svg", { viewBox: "0 0 100 4", preserveAspectRatio: "none" }, [
        svg(doc, "defs", {}, [gradient(doc, `pc${n}`, [[0, "rgba(96,165,250,0)"], [0.15, "#60a5fa"], [0.85, "#8b5cf6"], [1, "rgba(139,92,246,0)"]], { y2: 0 })]),
        svg(doc, "rect", { x: 0, y: 1, width: 100, height: 2, rx: 1, fill: `url(#pc${n})` }),
      ]);
    },
    racket(doc, n) {
      return svg(doc, "svg", { viewBox: "0 0 20 50" }, [
        svg(doc, "defs", {}, [gradient(doc, `pr${n}`, [[0, "#38bdf8"], [1, "#8b5cf6"]])]),
        svg(doc, "path", { d: "M10 49V30", stroke: "#e0e7ff", "stroke-width": 3, "stroke-linecap": "round" }),
        svg(doc, "ellipse", { cx: 10, cy: 15, rx: 8.4, ry: 13.4, fill: "rgba(56,189,248,0.12)", stroke: `url(#pr${n})`, "stroke-width": 2.4 }),
        svg(doc, "path", { d: "M5 6v18M10 2.5v25M15 6v18M3 10h14M2 15h16M3 20h14", stroke: "#bae6fd", "stroke-width": 0.6, opacity: 0.8 }),
      ]);
    },
  };
  function makeProp(doc, kind) {
    const box = node(doc, "span", `pa-prop pa-prop-${kind}`);
    const draw = PROPS[kind];
    if (draw) box.appendChild(draw(doc, (made += 1)));
    return box;
  }

  const propTransform = (f) => `translate3d(${f.x}px, ${f.y}px, 0) rotate(${f.r || 0}deg) scale(${f.sx === undefined ? 1 : f.sx}, ${f.sy === undefined ? 1 : f.sy})`;

  const bodyTransform = (f) => `rotate(${f.r}deg) scale(${f.sx}, ${f.sy})`;
  const placeTransform = (x, y) => `translate3d(${x}px, ${y}px, 0)`;

  // opts: {doc, stage (the element whose top-left is 0,0), art: {body, head,
  // eyes}, poses: {name: url}, extra: [elements to carry on the resting robot],
  // x, y, reduced}
  function create(opts) {
    const o = opts || {};
    const doc = o.doc || root.document;
    const stage = o.stage;
    const art = o.art || {};
    let poses = { ...(o.poses || {}) };
    const pos = node(doc, "div", "pa");
    const body = node(doc, "div", "pa-body");
    const rest = node(doc, "div", "pa-rest");
    const head = node(doc, "div", "pa-head");
    const eyes = picture(doc, "pa-eyes", art.eyes);
    rest.appendChild(picture(doc, "pa-rest-body", art.body));
    head.appendChild(picture(doc, "pa-rest-head", art.head));
    head.appendChild(eyes);
    rest.appendChild(head);
    for (const e of o.extra || []) rest.appendChild(e);
    body.appendChild(rest);
    pos.appendChild(body);
    stage.appendChild(pos);

    const shown = new Map(); // pose name → <img>
    let current = M().REST;
    let at = { x: o.x || 0, y: o.y || 0 };
    let running = null; // {anims, timers, resolve}
    let idleOn = false;
    let idleTimers = [];
    let awakeUntil = 0;
    let looking = null;
    let propEls = [];

    // Props live only during their move: all gone when it ends or stops.
    function clearProps() {
      for (const el of propEls) el.remove();
      propEls = [];
    }

    function place(x, y) {
      at = { x, y };
      pos.style.transform = placeTransform(x, y);
    }
    place(at.x, at.y);

    function poseImg(name) {
      if (!poses[name]) return null;
      if (!shown.has(name)) {
        const img = picture(doc, "pa-pose", poses[name]);
        img.dataset.pose = name;
        body.appendChild(img);
        shown.set(name, img);
      }
      return shown.get(name);
    }

    // Fetch the pose pictures a plan needs before it starts, so none pops in late.
    function preload(names) {
      const list = [...new Set(names)].filter((n) => n !== M().REST).map(poseImg).filter(Boolean);
      return Promise.all(list.map((img) => (img.decode ? img.decode().catch(() => {}) : Promise.resolve())));
    }

    function show(name, fade) {
      const next = name === M().REST ? rest : poseImg(name);
      const prev = current === M().REST ? rest : shown.get(current);
      if (!next) return;
      current = name;
      if (next === prev) return;
      if (next === rest) rest.classList.remove("pa-off"); else next.classList.add("pa-on");
      if (!prev) return;
      if (fade > 0 && next.animate) {
        next.animate([{ opacity: 0 }, { opacity: 1 }], { duration: fade, easing: "ease-out" });
        const out = prev.animate([{ opacity: 1 }, { opacity: 0 }], { duration: fade, easing: "ease-in", fill: "forwards" });
        out.onfinish = () => { hide(prev); out.cancel(); };
      } else hide(prev);
    }

    // Hide a picture unless it is the one showing now.
    function hide(el) {
      const now = current === M().REST ? rest : shown.get(current);
      if (el === now) return;
      if (el === rest) rest.classList.add("pa-off");
      else el.classList.remove("pa-on");
    }

    function spawn(fx) {
      const box = node(doc, "span", `pa-fx pa-fx-${fx.kind}`);
      const inner = node(doc, "span", "pa-fx-in");
      if (fx.text) inner.textContent = fx.text;
      if (fx.dir < 0) box.classList.add("pa-fx-left");
      box.appendChild(inner);
      box.style.transform = placeTransform(fx.x, fx.y);
      stage.appendChild(box);
      const done = () => box.remove();
      inner.addEventListener("animationend", done, { once: true });
      setTimeout(done, 2600); // in case animations are off
    }

    function current_place() {
      const t = root.getComputedStyle ? root.getComputedStyle(pos).transform : "";
      const m = /matrix(?:3d)?\(([^)]+)\)/.exec(t || "");
      if (!m) return { ...at };
      const v = m[1].split(",").map(Number);
      return v.length === 16 ? { x: v[12], y: v[13] } : { x: v[4], y: v[5] };
    }

    // Stop whatever is playing; the robot stays where it is, resting.
    function stop() {
      if (!running) return { ...at };
      const here = current_place();
      const r = running;
      running = null;
      for (const t of r.timers) clearTimeout(t);
      for (const a of r.anims) { try { a.cancel(); } catch (_) { /* gone */ } }
      place(Math.round(here.x), Math.round(here.y));
      clearProps();
      body.style.transform = "";
      head.style.transform = "";
      eyes.style.transform = "";
      show(M().REST, 0);
      r.resolve({ stopped: true, x: at.x, y: at.y });
      return { ...at };
    }

    // The animations for a plan, and its props, on the compositor.
    function build(plan) {
      const D = plan.duration;
      const frames = plan.frames;
      const off = (t) => Math.min(1, Math.max(0, t / D));
      const ease = (i) => (frames[i + 1] ? frames[i + 1].e : "linear");
      const anims = [];
      const opt = { duration: D, fill: "forwards" };
      anims.push(pos.animate(frames.map((f, i) => ({ offset: off(f.t), transform: placeTransform(f.x, f.y), easing: ease(i) })), opt));
      anims.push(body.animate(frames.map((f, i) => ({ offset: off(f.t), transform: bodyTransform(f), easing: ease(i) })), opt));
      const part = (el, list, fn) => {
        if (!list.length) return;
        const kf = [{ offset: 0, transform: fn({ r: 0, x: 0, y: 0 }) }];
        for (const s of list) {
          kf.push({ offset: off(s.t), transform: kf[kf.length - 1].transform, easing: s.e });
          kf.push({ offset: off(s.t + s.ms), transform: fn(s) });
        }
        kf.push({ offset: 1, transform: kf[kf.length - 1].transform });
        anims.push(el.animate(kf, opt));
      };
      const u = rest.getBoundingClientRect().width || 100;
      part(head, plan.head, (s) => `translateY(${s.y || 0}px) rotate(${s.r || 0}deg)`);
      part(eyes, plan.eyes, (s) => `translate(${(s.x || 0) * u}px, ${(s.y || 0) * u}px)`);
      clearProps();
      for (const pr of plan.props || []) {
        if (!pr.frames.length) continue;
        const el = makeProp(doc, pr.kind);
        (pr.on === "body" ? body : stage).appendChild(el);
        propEls.push(el);
        const kf = [];
        const first = pr.frames[0];
        const op = (f) => (f.o === undefined ? 1 : f.o);
        if (first.t > 0) kf.push({ offset: 0, transform: propTransform(first), opacity: 0, easing: "linear" });
        pr.frames.forEach((f, i) => kf.push({ offset: off(f.t), transform: propTransform(f), opacity: op(f),
          easing: pr.frames[i + 1] ? pr.frames[i + 1].e : "linear" }));
        const last = pr.frames[pr.frames.length - 1];
        if (off(last.t) < 1) kf.push({ offset: 1, transform: propTransform(last), opacity: op(last) });
        anims.push(el.animate(kf, opt));
      }
      return anims;
    }

    function finish(plan, anims) {
      place(plan.end.x, plan.end.y);
      clearProps();
      body.style.transform = "";
      for (const a of anims) { try { a.cancel(); } catch (_) { /* gone */ } }
      show(plan.endPose || M().REST, 0);
    }

    // Play a plan from pilot-motion.js. Resolves {x, y} where it ended.
    async function play(plan) {
      if (!plan) return { ...at };
      stop();
      cancelIdle();
      await preload(plan.poses.map((p) => p.pose));
      return new Promise((resolve) => {
        const timers = [];
        running = { anims: [], timers, resolve };
        running.anims = build(plan);
        const anims = running.anims;
        for (const p of plan.poses) {
          if (p.t === 0) { show(p.pose, 0); continue; }
          timers.push(setTimeout(() => show(p.pose, p.fade), p.t));
        }
        for (const f of plan.fx) timers.push(setTimeout(() => spawn(f), f.t));
        timers.push(setTimeout(() => {
          if (!running || running.resolve !== resolve) return;
          running = null;
          finish(plan, anims);
          awakeUntil = Date.now() + 12000;
          if (idleOn) scheduleIdle(400);
          resolve({ x: at.x, y: at.y });
        }, plan.duration + 20));
      });
    }

    // A plan held still at any moment, for frame strips and checks:
    // seek(ms) shows exactly that moment (pose, props and effects), end()
    // puts the robot where the plan ends.
    async function scrub(plan) {
      stop();
      cancelIdle();
      await preload(plan.poses.map((p) => p.pose));
      const anims = build(plan);
      for (const a of anims) a.pause();
      let fxEls = [];
      return {
        seek(ms) {
          for (const a of anims) a.currentTime = ms;
          const p = plan.poses.filter((q) => q.t <= ms).pop();
          show(p ? p.pose : M().REST, 0);
          for (const e of fxEls) e.remove();
          fxEls = [];
          for (const f of plan.fx) {
            if (f.t > ms || ms - f.t > 1900) continue;
            spawn(f);
            const box = stage.lastChild;
            fxEls.push(box);
            for (const a of box.getAnimations ? box.getAnimations({ subtree: true }) : []) { a.pause(); a.currentTime = ms - f.t; }
          }
        },
        end() { for (const e of fxEls) e.remove(); finish(plan, anims); },
      };
    }

    // ------------------------------------------------------------ resting

    function blink() {
      if (running || current !== M().REST || !eyes.animate) return;
      eyes.animate([{ transform: "scaleY(1)" }, { transform: "scaleY(0.1)", offset: 0.45 }, { transform: "scaleY(1)" }],
        { duration: 170, easing: "ease-in-out", composite: "add" });
    }

    function breathe(times) {
      if (running || current !== M().REST || !rest.animate) return;
      rest.animate([{ transform: "scale(1, 1)" }, { transform: "scale(1.012, 1.022)", offset: 0.45 }, { transform: "scale(1, 1)" }],
        { duration: 3600, iterations: times || 1, easing: "ease-in-out" });
    }

    // Look toward a point on the stage; null looks straight ahead again.
    function lookAt(point) {
      if (running || current !== M().REST || !head.animate) return;
      if (looking) { try { looking.cancel(); } catch (_) { /* gone */ } }
      const g = geometry();
      const dx = point ? point.x - at.x : 0;
      const r = Math.max(-1, Math.min(1, dx / (g.u * 3))) * 9;
      looking = head.animate([{ transform: "rotate(0deg)" }, { transform: `rotate(${r}deg)`, offset: 0.25 },
        { transform: `rotate(${r}deg)`, offset: 0.75 }, { transform: "rotate(0deg)" }], { duration: 2400, easing: "ease-in-out" });
    }

    function cancelIdle() {
      for (const t of idleTimers) clearTimeout(t);
      idleTimers = [];
    }

    function scheduleIdle(first) {
      cancelIdle();
      if (!idleOn) return;
      const blinkLater = (ms) => idleTimers.push(setTimeout(() => {
        blink();
        if (Math.random() < 0.2) setTimeout(blink, 260);
        blinkLater(3500 + Math.random() * 5500);
      }, ms));
      const breathLater = (ms) => idleTimers.push(setTimeout(() => {
        const awake = Date.now() < awakeUntil;
        breathe(awake ? 2 : 1);
        breathLater(awake ? 7200 : 25000 + Math.random() * 20000);
      }, ms));
      blinkLater(first || 1500);
      breathLater((first || 0) + 600);
    }

    function idle(on) {
      idleOn = Boolean(on) && !o.reduced;
      if (idleOn) { awakeUntil = Date.now() + 12000; scheduleIdle(800); } else cancelIdle();
    }

    // The robot's size and where it is, for planning (u: its resting width).
    function geometry() {
      const box = rest.getBoundingClientRect();
      return { u: box.width || 100, pos: { ...at } };
    }

    // Where the robot is drawn now (the visible picture), in page pixels.
    function rect() {
      const el = current === M().REST ? rest : shown.get(current) || rest;
      return el.getBoundingClientRect();
    }

    return {
      VERSION,
      el: pos,
      get busy() { return Boolean(running); },
      get pose() { return current; },
      get at() { return { ...at }; },
      get props() { return propEls.length; },
      poses: () => Object.keys(poses),
      setPoses(map) { poses = { ...(map || {}) }; },
      play, scrub, stop, place, blink, breathe, lookAt, idle, geometry, rect, show, preload,
      destroy() { stop(); idle(false); pos.remove(); },
    };
  }

  // ---------------------------------------------------------------- the web robot

  // The web robot wanders near its corner now and then: strolls along the
  // bottom, tricks, peeks and waves; flights and climbing only on Playful.
  // Never under "reduce motion" (the loader doesn't even fetch this file
  // then), never while the window is open, and nothing while the tab is
  // hidden. opts: {root, launcher, art, poses, store, corner(), isOpen(), dot}
  function live(opts) {
    const o = opts || {};
    const win = root;
    if (!win.matchMedia || win.matchMedia("(prefers-reduced-motion: reduce)").matches) return null;
    const doc = o.doc || win.document;
    const stage = node(doc, "span", "pa-stage");
    o.launcher.appendChild(stage);
    const actor = create({ doc, stage, art: o.art, poses: o.poses, extra: o.dot ? [o.dot] : [] });
    o.launcher.classList.add("pilot-alive");
    let timer = null;
    let greeted = false;

    const energy = () => {
      const e = o.store && o.store.ui ? o.store.ui().energy : "normal";
      return e || "normal";
    };
    const ctx = () => ({
      profile: "web",
      energy: energy() === "calm" ? "low" : energy(),
      calm: energy() === "calm" ? true : null,
      reducedMotion: win.matchMedia("(prefers-reduced-motion: reduce)").matches,
      chatOpen: Boolean(o.isOpen && o.isOpen()),
      hidden: doc.visibilityState === "hidden",
    });

    // The page in the robot's own coordinates: its home spot is 0,0.
    function geometry() {
      const g = actor.geometry();
      const box = o.launcher.getBoundingClientRect();
      const homeX = box.left + box.width / 2;
      const homeY = box.bottom;
      const gap = 12;
      const corner = o.corner ? o.corner() : "br";
      const bottom = corner[0] === "b";
      return {
        u: g.u, pos: { x: 0, y: 0 }, home: { x: 0, y: 0 }, floor: 0, profile: "web", random: Math.random,
        left: -(homeX - gap - g.u * 0.6), right: win.innerWidth - homeX - gap - g.u * 0.6,
        top: -(homeY - gap - g.u * 1.6), climb: win.innerHeight * 0.3, depth: box.height + 24,
        sky: win.innerHeight * 0.3, energy: energy(), atFloor: bottom, sides: true, poses: actor.poses(),
      };
    }

    async function act(name, extra) {
      const c = ctx();
      if (actor.busy) return;
      const g = geometry();
      if (!g.atFloor && !["trick", "backflip", "spin", "bounce", "wave", "look", "celebrate", "point", "love", "power", "wait"].includes(name)) return;
      // Something to say: no key yet.
      const more = name === "point" && o.store && o.store.ready && !o.store.ready() ? { say: "🔑", ...(extra || {}) } : extra;
      const plan = M().plan(name, g, more);
      if (!plan) return;
      if (name !== "wave" && M().stillReason(c)) return;
      await actor.play(plan);
      actor.place(0, 0);
    }

    function later() {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        const c = { ...ctx(), atFloor: geometry().atFloor };
        const name = M().pick(c);
        if (name) await act(name);
        later();
      }, M().nextDelay(ctx()));
    }

    function hello() {
      if (greeted) return;
      greeted = true;
      const how = M().reaction("hello", ctx());
      if (how && energy() !== "calm") setTimeout(() => act("wave", { how }), 2500);
    }

    doc.addEventListener("visibilitychange", () => {
      if (doc.visibilityState === "hidden") { clearTimeout(timer); actor.stop(); actor.place(0, 0); actor.idle(false); } else { actor.idle(true); later(); }
    });
    actor.idle(true);
    later();
    return {
      actor, act, hello,
      // The window opened or the robot is being dragged: stop and go home.
      settle() { actor.stop(); actor.place(0, 0); },
      reschedule: later,
    };
  }

  const api = { VERSION, create, live };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PilotActor = api;
})(typeof window !== "undefined" ? window : globalThis);
