// Pilot core 1.4.0 · pilot-motion.js
// How the robot moves: its poses, its moves, and when it does something.
// No DOM here, so the web robot, the Mac robot and the tests share it;
// pilot-actor.js plays a plan on the page.
//
// Poses. Each moment of a move names the pose picture it wants: standing,
// sitting, wave, walk1, walk2, run1, run2, fly, climb1, climb2, flip, throw,
// kick, tennis, celebrate, slide, point, power, dash, love, land or
// arms-crossed. An app
// tells the engine which pictures it has (on the Mac, every <pose>.webp or
// .png in assets/poses/; on the web, static/pilot/poses/). Some pictures hold
// their own prop (throw: a football, kick: a soccer ball, tennis: a racket
// and ball); the moves hand over to the drawn props at the right moment. A pose without a
// picture falls back along FALLBACK, and in the end to "rest": the owner's
// own design robot, moved by transforms. So a new pose picture is picked up
// just by being there, and every move still works without it.
//
// Plans. A move becomes a plan: keyframes for the robot's place (x, y: the
// point between its feet), its turn and squash (r, sx, sy), its head (on the
// resting robot), its eyes, the pose to show from each moment, and effects
// (dust, a glow trail, sparkles, a 👋 bubble). Times are in milliseconds;
// easings are CSS easings, so a page plays a plan with the Web Animations
// API on the compositor and no script runs per frame.
//
// When. pick() chooses the next move, or none. Nothing wanders in calm mode,
// under "reduce motion" (calm by default), while the chat is open, while the
// user is typing, while something is full screen in front, or while the robot
// is hidden or being dragged. Most of the time the robot just rests.
(function (root) {
  "use strict";

  const VERSION = "1.4.0";
  const REST = "rest";
  const POSES = ["standing", "sitting", "wave", "walk1", "walk2", "run1", "run2", "fly", "climb1", "climb2", "flip",
    "throw", "kick", "tennis", "celebrate", "slide", "point", "power", "dash", "love", "land", "arms-crossed"];
  // What a pose without a picture shows instead (first one there wins), and
  // in the end the resting design robot. walk2 is the standing picture
  // between walk1 steps; climb2 is climb1 nudged higher; run2 is run1.
  const FALLBACK = {
    standing: [], sitting: [], wave: [], walk1: [], walk2: ["standing", "walk1"], run1: ["walk1"],
    run2: ["walk1", "run1"], fly: [], climb1: [], climb2: ["climb1"], flip: [],
    // The sports poses, until their pictures arrive: the football is thrown
    // from the waving hand, the kick is a leaning run2, and tennis is the
    // standing robot with a drawn racket in its hand.
    throw: ["wave", "standing"], kick: ["run2", "run1"], tennis: ["standing"],
    // More poses on the way: until their pictures arrive, a cheer is the
    // standing robot, a dash is run1, and a power-up crouch is standing.
    // slide, point and love are used only when their pictures exist.
    celebrate: ["standing"], dash: ["run1", "walk1"], power: ["standing"], slide: [], point: [], love: [],
    land: ["standing"], "arms-crossed": [],
  };
  const EASE = {
    linear: "linear",
    soft: "cubic-bezier(0.37, 0, 0.63, 1)",
    out: "cubic-bezier(0.22, 1, 0.36, 1)",
    in: "cubic-bezier(0.55, 0, 1, 0.45)",
    pop: "cubic-bezier(0.34, 1.56, 0.64, 1)",
  };
  const ENERGY = ["low", "normal", "playful"];
  // Seconds between moves, at random within the range.
  const GAPS = {
    desktop: { low: [70, 160], normal: [30, 120], playful: [15, 60] },
    web: { low: [100, 240], normal: [50, 150], playful: [25, 80] },
  };
  // How often each move is chosen. The web robot stays near its corner:
  // no climbing, running or flights unless the user picks Playful.
  const WEIGHTS = {
    desktop: {
      low: { walk: 30, fly: 8, trick: 18, peek: 10, wave: 10, look: 24, home: 30, sport: 2, celebrate: 3, love: 1 },
      normal: { walk: 24, run: 8, fly: 14, climb: 9, trick: 18, peek: 9, wave: 8, look: 12, home: 26, sport: 5, celebrate: 4, power: 3,
        slide: 3, love: 1 },
      playful: { walk: 18, run: 14, fly: 18, climb: 14, trick: 22, peek: 10, wave: 6, look: 6, home: 22, sport: 14, celebrate: 5,
        power: 6, slide: 6, love: 1 },
    },
    web: {
      low: { stroll: 30, trick: 20, peek: 15, wave: 20, look: 15, sport: 2, celebrate: 5, love: 2, point: 3 },
      normal: { stroll: 30, trick: 28, peek: 16, wave: 16, look: 10, sport: 4, celebrate: 7, love: 2, point: 3 },
      playful: { stroll: 22, run: 10, trick: 26, peek: 12, wave: 10, fly: 12, climb: 10, sport: 12, celebrate: 6, love: 2, point: 2,
        slide: 6, power: 6 },
    },
  };
  const TRICKS = ["backflip", "spin", "bounce"];
  // The sports tricks: rarer than the rest. On the web, soccer and tennis
  // only on Playful.
  const SPORTS = ["football", "soccer", "tennis"];
  // Where the hands are in each pose picture, in resting-robot widths from
  // the point between the feet (x right, y down). A sports prop (the
  // football, the racket) is held there. "near" is the hand on the right of
  // the picture, "far" the one on the left, "up" a raised hand.
  const HANDS = {
    standing: { near: [0.327, -0.382], far: [-0.191, -0.382] },
    wave: { up: [-0.323, -0.887], near: [0.286, -0.368] },
    // The props drawn into pictures: where the football, the soccer ball,
    // the tennis ball and the energy ball are (measured from the pictures).
    throw: { up: [-0.498, -0.888], near: [0.3, -0.4] },
    kick: { ball: [0.553, -0.485] },
    tennis: { ball: [0.542, -0.746], near: [0.45, -0.7], up: [0.45, -0.85] },
    power: { ball: [-0.298, -0.867] },
    rest: { up: [0.0, -0.75], near: [0.32, -0.3], far: [-0.32, -0.3] },
  };

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const round = (v) => Math.round(v * 10) / 10;

  // ---------------------------------------------------------------- poses

  function poseSet(available) {
    if (available instanceof Set) return available;
    if (Array.isArray(available)) return new Set(available);
    return new Set(Object.keys(available || {}));
  }

  // The picture to show for a pose: itself if there, else its fallback, else
  // the resting robot.
  function resolvePose(name, available) {
    if (name === REST || !POSES.includes(name)) return REST;
    const have = poseSet(available);
    if (have.has(name)) return name;
    for (const f of FALLBACK[name] || []) if (have.has(f)) return f;
    return REST;
  }

  // Pose pictures from a list of file names: "wave.webp" → {wave: "wave.webp"}.
  // Only the names above count; WebP wins over PNG for the same pose.
  function posesFromFiles(files) {
    const out = {};
    for (const f of files || []) {
      const m = /^([a-z0-9-]+)\.(webp|png)$/.exec(String(f));
      if (!m || !POSES.includes(m[1])) continue;
      if (!out[m[1]] || m[2] === "webp") out[m[1]] = f;
    }
    return out;
  }

  // ---------------------------------------------------------------- building a plan

  function track(start, have) {
    const s = { x: start.x, y: start.y, r: 0, sx: 1, sy: 1 };
    const plan = { frames: [{ t: 0, ...s, e: EASE.soft }], poses: [{ t: 0, pose: REST, fade: 0 }], head: [], eyes: [], fx: [], props: [] };
    const props = {};
    let t = 0;
    let pose = REST;
    const k = {
      get t() { return t; },
      get now() { return { ...s }; },
      get pose() { return pose; },
      has: (name) => resolvePose(name, have) === name,
      shows: (name) => resolvePose(name, have),
      // Move to new values over ms with an easing.
      to(ms, vals, ease) {
        t += Math.max(0, ms);
        Object.assign(s, vals);
        plan.frames.push({ t, ...s, e: EASE[ease] || ease || EASE.soft });
        return k;
      },
      // Jump at once (two keyframes at the same moment).
      set(vals) { return k.to(0, vals, "linear"); },
      wait(ms) { return k.to(ms, {}, "linear"); },
      // Show a pose from now (after fallbacks). fade: a short cross-fade.
      show(name, fade) {
        const p = resolvePose(name, have);
        if (p !== pose) { plan.poses.push({ t, pose: p, fade: fade || 0 }); pose = p; }
        return k;
      },
      head(ms, r, y, ease) { plan.head.push({ t, ms, r, y: y || 0, e: EASE[ease] || EASE.soft }); return k; },
      eyes(ms, x, y, ease) { plan.eyes.push({ t, ms, x, y, e: EASE[ease] || EASE.soft }); return k; },
      fx(kind, dx, dy, extra) { plan.fx.push({ t, kind, x: round(s.x + (dx || 0)), y: round(s.y + (dy || 0)), ...(extra || {}) }); return k; },
      fxAt(at, kind, x, y, extra) { plan.fx.push({ t: Math.max(0, at), kind, x: round(x), y: round(y), ...(extra || {}) }); return k; },
      // A prop (football, soccer ball, goal, racket…) that lives only during
      // this move. on: "stage" (placed on the page) or "body" (held: moves
      // with the robot, placed from the point between its feet).
      prop(id, kind, on) {
        props[id] = { id, kind, on: on || "stage", frames: [], last: { x: 0, y: 0, r: 0, sx: 1, sy: 1, o: 0 } };
        plan.props.push(props[id]);
        return k;
      },
      // A prop's place at an absolute time (props run alongside the robot).
      propAt(id, at, vals, ease) {
        const p = props[id];
        Object.assign(p.last, vals);
        p.frames.push({ t: Math.max(0, at), ...p.last, e: EASE[ease] || ease || EASE.linear });
        return k;
      },
      done(name) {
        plan.name = name;
        plan.duration = Math.max(1, t);
        plan.end = { x: round(s.x), y: round(s.y) };
        plan.endPose = pose;
        for (const p of plan.props) {
          p.frames.sort((a, b) => a.t - b.t);
          p.frames = p.frames.map((f) => ({ ...f, x: round(f.x), y: round(f.y), r: round(f.r) }));
          delete p.last;
        }
        plan.frames = plan.frames.map((f) => ({ ...f, x: round(f.x), y: round(f.y), r: round(f.r), sx: round(f.sx * 100) / 100, sy: round(f.sy * 100) / 100 }));
        return plan;
      },
    };
    return k;
  }

  // From resting to standing, with a little squash, so the swap from the
  // design robot to the pose pictures reads as standing up, not a pop.
  function standUp(k, pose) {
    const p = k.shows(pose || "standing");
    if (p === REST) return k.to(140, { sy: 0.92, sx: 1.05 }, "soft").to(140, { sy: 1, sx: 1 }, "out");
    return k.to(80, { sy: 0.9, sx: 1.06 }, "in").show(pose || "standing", 150).to(150, { sy: 1.03, sx: 0.98 }, "out").to(90, { sy: 1, sx: 1 }, "soft");
  }

  function sitDown(k) {
    if (k.pose === REST) return k.to(120, { sy: 0.94, sx: 1.04, r: 0 }, "soft").to(160, { sy: 1, sx: 1 }, "out");
    return k.to(100, { sy: 0.9, sx: 1.06, r: 0 }, "in").show(REST, 150).to(170, { sy: 1.02, sx: 0.99 }, "out").to(90, { sy: 1, sx: 1 }, "soft");
  }

  // Hop-walk (or run) along the floor to x.
  function steps(k, g, toX, run) {
    const dist = toX - k.now.x;
    const dir = Math.sign(dist) || 1;
    const len = g.u * (run ? 0.42 : 0.26);
    const n = Math.max(1, Math.round(Math.abs(dist) / len));
    const each = dist / n;
    const ms = run ? 190 : 300;
    const hop = g.u * (run ? 0.12 : 0.07);
    const lean = dir * (run ? 7 : 4);
    for (let i = 0; i < n; i += 1) {
      const a = i % 2 === 0;
      k.show(run ? (i === 0 ? "dash" : a ? "run1" : "run2") : (a ? "walk1" : "walk2"));
      k.to(ms * 0.5, { x: k.now.x + each / 2, y: g.floor - hop, r: lean, sx: 0.98, sy: 1.04 }, "out");
      k.to(ms * 0.5, { x: k.now.x + each / 2, y: g.floor, sx: 1.04, sy: 0.95 }, "in");
      if (run || i % 2 === 1 || i === n - 1) k.fx("dust", -dir * g.u * 0.12, 0, { dir });
    }
    k.show("standing").to(120, { r: 0, sx: 1, sy: 1 }, "out");
    return k;
  }

  function atFloor(g, y) { return Math.abs((y === undefined ? g.pos.y : y) - g.floor) < g.u * 0.08; }

  function walk(g, toX, run) {
    const k = track(g.pos, g.poses);
    const x = clamp(toX, g.left, g.right);
    standUp(k, run ? "run1" : "walk1");
    steps(k, g, x, run);
    sitDown(k);
    return k.done(run ? "run" : "walk");
  }

  // Walk out a little way and back (the web robot's stroll).
  function stroll(g, toX) {
    const k = track(g.pos, g.poses);
    const start = g.pos.x;
    standUp(k, "walk1");
    steps(k, g, clamp(toX, g.left, g.right), false);
    k.wait(500);
    steps(k, g, start, false);
    sitDown(k);
    return k.done("stroll");
  }

  // Fly along a soft arc to a spot, with a blue glow trail.
  function fly(g, to, back) {
    const k = track(g.pos, g.poses);
    const from = { ...g.pos };
    const dest = { x: clamp(to.x, g.left, g.right), y: clamp(to.y, g.top, g.floor) };
    standUp(k);
    k.to(130, { sy: 0.88, sx: 1.08 }, "in");
    k.show("fly", 120).to(240, { y: k.now.y - g.u * 0.3, sy: 1.08, sx: 0.96 }, "out").fx("trail", 0, -g.u * 0.1);
    const leg = (a, b) => {
      const dx = b.x - a.x;
      const dist = Math.hypot(dx, b.y - a.y);
      const lift = g.u * (0.5 + Math.min(1, dist / (g.u * 8)));
      const cx = (a.x + b.x) / 2;
      const cy = Math.min(a.y, b.y) - lift;
      const total = clamp(dist * 3.2, 1200, 3200);
      const n = 8;
      const dir = Math.sign(dx) || 1;
      for (let i = 1; i <= n; i += 1) {
        const u = i / n;
        const x = (1 - u) * (1 - u) * a.x + 2 * (1 - u) * u * cx + u * u * b.x;
        const y = (1 - u) * (1 - u) * a.y + 2 * (1 - u) * u * cy + u * u * b.y;
        const sway = Math.sin(u * Math.PI * 2) * 3;
        k.to(total / n, { x, y, r: dir * 6 * Math.sin(u * Math.PI) + sway, sx: 1, sy: 1 }, i === 1 ? "in" : i === n ? "out" : "linear");
        k.fx("trail", 0, -g.u * 0.1);
      }
    };
    leg({ x: k.now.x, y: k.now.y }, { x: dest.x, y: dest.y - g.u * 0.3 });
    if (back) {
      k.wait(600);
      leg({ x: k.now.x, y: k.now.y }, { x: from.x, y: from.y - g.u * 0.3 });
    }
    const land = back ? from : dest;
    k.to(320, { x: land.x, y: land.y, r: 0 }, "soft");
    if (k.has("land")) {
      // A superhero landing.
      k.show("land", 60).to(120, { sy: 0.9, sx: 1.06 }, "out").fx("dust", 0, 0).fx("dust", -g.u * 0.3, 0, { dir: -1 }).fx("sparkle", g.u * 0.5, -g.u * 0.5);
      k.to(160, { sy: 1, sx: 1 }, "out").wait(520).show("standing", 120);
    }
    k.show("standing").to(110, { sy: 0.88, sx: 1.08 }, "out").fx("dust", 0, 0);
    k.to(150, { sy: 1, sx: 1 }, "out");
    sitDown(k);
    return k.done("fly");
  }

  // Climb up a side edge in small steps, look around, and hop back down.
  function climb(g, side) {
    const k = track(g.pos, g.poses);
    const edge = side < 0 ? g.left : g.right;
    const height = clamp(g.climb || (g.floor - g.top) * 0.45, g.u * 1.2, g.floor - g.top - g.u * 1.6);
    const stepH = g.u * 0.24;
    const n = Math.max(3, Math.round(height / stepH));
    if (Math.abs(edge - g.pos.x) > g.u * 0.3) { standUp(k, "walk1"); steps(k, g, edge, false); }
    if (k.has("climb1")) {
      // The climbing picture: upright against the edge, nudged up step by step.
      if (k.pose === REST) standUp(k, "climb1");
      k.show("climb1", 120).to(160, { x: edge + side * g.u * 0.05, sy: 0.96 }, "soft");
      for (let i = 0; i < n; i += 1) {
        k.show(i % 2 ? "climb2" : "climb1");
        k.to(170, { y: k.now.y - stepH * 0.7, r: side * 3, sy: 1.04 }, "out");
        k.to(210, { y: k.now.y - stepH * 0.3, r: -side * 2, sy: 1 }, "soft");
      }
      k.wait(700).to(240, { r: side * -6 }, "soft").wait(400).to(240, { r: 0 }, "soft");
      k.show("standing").to(520, { y: g.floor, x: edge - side * g.u * 0.25, r: 0 }, "in");
    } else {
      // No climbing picture yet: the resting robot turns onto the edge, its
      // feet against it, and inches up.
      if (k.pose !== REST) sitDown(k);
      k.to(260, { x: edge, r: -side * 90, sy: 0.94 }, "out").to(120, { sy: 1 }, "soft");
      for (let i = 0; i < n; i += 1) {
        k.to(160, { y: k.now.y - stepH * 0.6, sy: 1.06, sx: 0.95 }, "out");
        k.to(200, { y: k.now.y - stepH * 0.4, sy: 0.96, sx: 1.03 }, "soft");
      }
      k.to(160, { sy: 1, sx: 1 }).head(400, side * 12, 0).wait(900).head(300, 0, 0).wait(300);
      k.to(560, { y: g.floor, x: edge - side * g.u * 0.3, r: 0 }, "in");
    }
    k.to(110, { sy: 0.88, sx: 1.08 }, "out").fx("dust", 0, 0).to(150, { sy: 1, sx: 1 }, "out");
    sitDown(k);
    comeBack(k, g, g.pos);
    if (k.pose !== REST) sitDown(k);
    return k.done("climb");
  }

  // A backflip. The body turns about its middle (hc above the feet): each
  // keyframe moves the feet so the middle stays put. The flip picture shows
  // the upside-down moment.
  function backflip(g) {
    const k = track(g.pos, g.poses);
    standUp(k);
    const base = { ...k.now };
    const pic = k.has("flip");
    const hc = g.u * (k.pose === REST ? 0.45 : 0.72);
    const up = g.u * 0.95;
    const stood = k.pose;
    k.show("power").to(140, { sy: 0.86, sx: 1.08 }, "in");
    k.show(stood).to(200, { y: base.y - up * 0.6, sy: 1.08, sx: 0.95 }, "out");
    const turns = 12;
    const spin = 640;
    const still = k.pose;
    for (let i = 1; i <= turns; i += 1) {
      const deg = -360 * (i / turns);
      const want = pic && deg < -75 && deg > -285 ? "flip" : still;
      const rad = (deg * Math.PI) / 180;
      const lift = Math.sin((i / turns) * Math.PI) * up * 0.4 + up * 0.6 * (1 - Math.max(0, (i / turns - 0.7) / 0.3));
      if (want !== k.pose) {
        // Swap the picture at the angle it is at, turned so nothing jumps:
        // the flip picture is already upside down.
        const prev = deg + 360 / turns;
        k.show(want).set({ r: want === "flip" ? prev + 180 : prev });
      }
      k.to(spin / turns, { x: base.x - hc * Math.sin(rad), y: base.y - lift + hc * (Math.cos(rad) - 1),
        r: want === "flip" ? deg + 180 : deg, sx: 1, sy: 1 }, "linear");
    }
    k.set({ r: 0 }).to(170, { x: base.x, y: base.y, sy: 0.86, sx: 1.1 }, "in").fx("dust", 0, 0).fx("sparkle", 0, -g.u * 1.2);
    k.to(180, { sy: 1.03, sx: 0.98 }, "out").to(100, { sy: 1, sx: 1 }, "soft");
    if (k.pose !== REST && k.has("celebrate")) k.show("celebrate", 100).wait(700).show("standing", 100);
    sitDown(k);
    return k.done("backflip");
  }

  // A turn on the spot: squeezed edge-on and back twice, never mirrored
  // (the P on its chest would come out backwards).
  function spin(g) {
    const k = track(g.pos, g.poses);
    standUp(k);
    const y = k.now.y;
    k.to(110, { sy: 0.92, sx: 1.04 }, "in").to(180, { y: y - g.u * 0.22, sy: 1.04, sx: 0.5 }, "out");
    k.to(130, { sx: 0.08 }, "in").to(130, { sx: 1 }, "out").to(130, { sx: 0.08 }, "in").to(140, { sx: 1 }, "out");
    k.to(200, { y, sy: 0.9, sx: 1.06 }, "in").fx("sparkle", g.u * 0.4, -g.u * 1.1).to(160, { sy: 1, sx: 1 }, "out");
    sitDown(k);
    return k.done("spin");
  }

  // Three happy bounces, smaller each time, the head a beat behind.
  function bounce(g) {
    const k = track(g.pos, g.poses);
    const y = g.pos.y;
    [0.55, 0.32, 0.15].forEach((h, i) => {
      k.to(110, { sy: 0.86, sx: 1.1 }, "in").head(160, 0, g.u * 0.02);
      k.to(220 - i * 30, { y: y - g.u * h, sy: 1.07, sx: 0.95 }, "out").head(200, 0, -g.u * 0.03);
      k.to(200 - i * 30, { y, sy: 1, sx: 1 }, "in");
    });
    k.to(130, { sy: 0.92, sx: 1.05 }, "out").head(200, 0, 0).to(160, { sy: 1, sx: 1 }, "out");
    return k.done("bounce");
  }

  // Peek: hide just past an edge, peek back with a look around, and come back.
  function peek(g) {
    const k = track(g.pos, g.poses);
    const start = { ...g.pos };
    const nearLeft = g.pos.x - g.left < g.u * 1.5;
    const nearRight = g.right - g.pos.x < g.u * 1.5;
    if ((nearLeft || nearRight) && g.sides !== false) {
      const side = nearLeft ? -1 : 1;
      const out = (side < 0 ? g.left : g.right) + side * g.u * 1.2;
      if (k.has("slide")) standUp(k, "slide");
      k.to(500, { x: out, r: side * 8 }, "in").show(REST).set({ sx: 1, sy: 1 }).wait(700);
      k.to(520, { x: out - side * g.u * 0.75, r: -side * 12 }, "out").head(300, -side * 10, 0).eyes(300, -side * 0.03, 0);
      k.wait(900).head(300, side * 4, 0).wait(500);
      k.to(520, { x: start.x, r: 0 }, "out").head(260, 0, 0).eyes(260, 0, 0);
    } else {
      const depth = g.depth === undefined ? g.u * 1.05 : g.depth;
      k.to(160, { sy: 0.92 }, "soft").to(420, { y: start.y + depth, sy: 1 }, "in").wait(600);
      k.to(460, { y: start.y + depth * 0.5 }, "out").head(300, -10, 0).eyes(300, -0.03, 0).wait(700);
      k.head(380, 10, 0).eyes(380, 0.03, 0).wait(700).head(260, 0, 0).eyes(260, 0, 0).wait(200);
      k.to(300, { y: start.y - g.u * 0.25, sy: 1.08, sx: 0.95 }, "out").to(240, { y: start.y, sy: 0.9, sx: 1.06 }, "in");
      k.fx("sparkle", g.u * 0.3, -g.u * 1.0).to(160, { sy: 1, sx: 1 }, "out");
    }
    return k.done("peek");
  }

  // A wave: the wave picture with a happy sway, or (without it) a cheerful
  // head tilt and bounce, and a 👋 bubble. gentle: no bounce; still: only
  // the bubble (under "reduce motion").
  function wave(g, how) {
    const k = track(g.pos, g.poses);
    const y = g.pos.y;
    const top = -g.u * (k.has("wave") && how !== "still" ? 1.65 : 1.1);
    if (how === "still") return k.fx("bubble", 0, top, { text: "👋" }).wait(1800).done("wave");
    if (how === "gentle" && k.has("love")) {
      standUp(k, "love");
      k.fx("bubble", 0, -g.u * 1.65, { text: "💙" }).to(400, { r: 3 }, "soft").to(400, { r: -2 }, "soft").to(300, { r: 0 }, "soft").wait(500);
      sitDown(k);
      return k.done("wave");
    }
    if (how === "gentle") {
      k.fx("bubble", 0, top, { text: "👋" }).head(360, 10, 0).wait(900).head(360, -6, 0).wait(500).head(300, 0, 0).wait(500);
      return k.done("wave");
    }
    if (k.has("wave")) {
      standUp(k, "wave");
      k.fx("bubble", 0, top, { text: "👋" }).fx("sparkle", g.u * 0.45, top * 0.8);
      for (let i = 0; i < 3; i += 1) k.to(220, { r: 5, y: y - g.u * 0.05 }, "soft").to(220, { r: -4, y }, "soft");
      k.to(160, { r: 0 }, "soft");
      sitDown(k);
    } else {
      k.fx("bubble", 0, top, { text: "👋" }).fx("sparkle", g.u * 0.45, top * 0.85);
      k.head(260, 12, 0).to(120, { sy: 0.92, sx: 1.05 }, "in").to(200, { y: y - g.u * 0.2, sy: 1.05, sx: 0.97 }, "out");
      k.to(200, { y, sy: 0.94, sx: 1.04 }, "in").to(140, { sy: 1, sx: 1 }, "out").head(260, -8, 0).wait(500);
      k.head(260, 10, 0).wait(500).head(300, 0, 0).wait(300);
    }
    return k.done("wave");
  }

  // Look toward a point (the mouse): head tilt and eyes, then back.
  function look(g, at) {
    const k = track(g.pos, g.poses);
    const dx = at ? at.x - g.pos.x : 0;
    const dy = at ? at.y - (g.pos.y - g.u * 0.6) : 0;
    const r = clamp(dx / (g.u * 3), -1, 1) * 9;
    const ex = clamp(dx / (g.u * 4), -1, 1) * 0.035;
    const ey = clamp(dy / (g.u * 4), -1, 1) * 0.025;
    if (k.has("point") && Math.abs(dx) > g.u * 4) {
      // Far away: it stands and points (the picture faces the way it was drawn).
      standUp(k, "point");
      k.to(300, { r: Math.sign(dx) * 3 }, "soft").wait(1100).to(200, { r: 0 }, "soft");
      sitDown(k);
      return k.done("look");
    }
    k.head(420, r, 0, "out").eyes(380, ex, ey, "out").wait(1500).head(500, 0, 0).eyes(450, 0, 0).wait(520);
    return k.done("look");
  }

  function nod(g) {
    const k = track(g.pos, g.poses);
    k.head(180, 0, g.u * 0.035, "soft").wait(200).head(180, 0, 0).wait(200).head(180, 0, g.u * 0.03).wait(200).head(200, 0, 0).wait(220);
    return k.done("nod");
  }

  // Back to its home spot: walking along the floor if both are on it.
  function home(g, h) {
    if (!h || Math.hypot(h.x - g.pos.x, h.y - g.pos.y) < g.u * 0.3) return null;
    if (atFloor(g) && atFloor(g, h.y)) return walk(g, h.x, Math.abs(h.x - g.pos.x) > (g.right - g.left) * 0.4);
    return fly(g, h, false);
  }

  // ---------------------------------------------------------------- sports tricks

  // Where a hand of the pose showing now is, on the page.
  function hand(k, g, which) {
    const set = HANDS[k.pose] || HANDS.rest;
    const h = set[which] || set.near || HANDS.rest.near;
    return { x: k.now.x + h[0] * g.u, y: k.now.y + h[1] * g.u };
  }

  const roomOf = (g) => ({ left: g.pos.x - g.left, right: g.right - g.pos.x });
  // The page's real side edges (left and right are where the robot's feet may go).
  const edgeOf = (g, dir) => (dir > 0 ? g.right + g.u * 0.6 : g.left - g.u * 0.6);
  const skyOf = (g) => Math.max(g.u, g.sky || g.floor - g.top);

  // Back to where it started (the web robot always ends at home).
  function comeBack(k, g, start) {
    if (g.profile !== "web" || Math.abs(k.now.x - start.x) < 2) return;
    if (k.pose === REST) standUp(k, "walk1");
    steps(k, g, start.x, false);
  }

  // Football: a spiral throw across the page, a bounce, and gone.
  function football(g) {
    const k = track(g.pos, g.poses);
    const start = { ...g.pos };
    const room = roomOf(g);
    const dir = room.right >= room.left ? 1 : -1;
    const held = k.has("throw"); // the picture holds its own ball
    standUp(k, "throw");
    k.prop("ball", "football");
    let h = hand(k, g, "up");
    k.propAt("ball", 0, { x: h.x, y: h.y, o: 0, r: -dir * 30 });
    if (!held) k.propAt("ball", k.t, { x: h.x, y: h.y, o: 1 });
    k.to(280, { r: -dir * 8, x: k.now.x - dir * g.u * 0.05 }, "soft");
    h = hand(k, g, "up");
    if (!held) k.propAt("ball", k.t, { x: h.x, y: h.y, r: -dir * 40 });
    k.to(150, { r: dir * 12, x: k.now.x + dir * g.u * 0.12 }, "in");
    h = hand(k, g, "up");
    const release = k.t;
    // The throw picture holds its own football: at the release it gives way
    // to the follow-through, and the drawn-in-code football flies from there.
    if (held) k.show("wave");
    k.propAt("ball", release, { x: h.x, y: h.y, o: 1, r: -dir * 20 });
    const dist = Math.min(Math.max(g.u * 3, (dir > 0 ? room.right : room.left) * 0.75), g.u * 9);
    const land = { x: start.x + dir * dist, y: g.floor - g.u * 0.08 };
    const apex = Math.min(g.u * 1.6 + dist * 0.22, skyOf(g) * 0.85);
    const flight = 1150;
    const n = 12;
    for (let i = 1; i <= n; i += 1) {
      const u = i / n;
      const x = h.x + (land.x - h.x) * u;
      const y = h.y + (land.y - h.y) * u - apex * 4 * u * (1 - u);
      const slope = (land.y - h.y) - apex * 4 * (1 - 2 * u);
      const angle = (Math.atan2(slope, (land.x - h.x)) * 180) / Math.PI;
      k.propAt("ball", release + flight * u, { x, y, r: angle, sx: i % 2 ? 0.62 : 1, sy: 1 });
    }
    // One bounce, a little roll, and it fades.
    k.propAt("ball", release + flight + 260, { x: land.x + dir * g.u * 0.5, y: land.y - g.u * 0.45, r: dir * 200, sx: 1 }, "out");
    k.propAt("ball", release + flight + 520, { x: land.x + dir * g.u * 0.9, y: land.y, r: dir * 300 }, "in");
    k.propAt("ball", release + flight + 900, { x: land.x + dir * g.u * 1.2, r: dir * 330, o: 0 }, "out");
    k.fxAt(release + flight, "dust", land.x, land.y + g.u * 0.08, { dir });
    k.wait(350).to(240, { r: 0, x: start.x }, "soft");
    k.wait(Math.max(0, release + flight + 700 - k.t)).fx("sparkle", g.u * 0.4, -g.u * 1.4);
    sitDown(k);
    comeBack(k, g, start);
    if (k.pose !== REST) sitDown(k);
    return k.done("football");
  }

  // Soccer: a goal slides in at one side, the robot dribbles and scores.
  function soccer(g) {
    const k = track(g.pos, g.poses);
    const start = { ...g.pos };
    const room = roomOf(g);
    const dir = room.right >= room.left ? 1 : -1;
    const edge = edgeOf(g, dir);
    const r = g.u * 0.06; // the ball's radius (as drawn in the kick picture)
    const goal = { x: edge - dir * g.u * 0.5, y: g.floor };
    k.prop("goal", "goal").prop("net", "net").prop("ball", "soccer");
    // The goal slides in from beyond the edge.
    const off = { x: edge + dir * g.u * 0.6 };
    for (const id of ["goal", "net"]) {
      k.propAt(id, 0, { x: off.x, y: goal.y, o: 1, sx: dir });
      k.propAt(id, 450, { x: goal.x, y: goal.y }, "out");
    }
    standUp(k, "run1");
    const ballY = g.floor - r;
    const dribble = Math.max(g.u * 0.6, Math.min(g.u * 2.6, Math.abs(goal.x - start.x) - g.u * 2.6));
    let bx = k.now.x + dir * g.u * 0.45;
    k.propAt("ball", 0, { x: bx, y: ballY, o: 0 }).propAt("ball", k.t, { x: bx, y: ballY, o: 1 });
    // Dribble: each run step taps the ball a little ahead.
    const nSteps = Math.max(2, Math.round(dribble / (g.u * 0.42)));
    const each = (dir * dribble) / nSteps;
    let spin = 0;
    for (let i = 0; i < nSteps; i += 1) {
      k.show(i % 2 ? "run2" : "run1");
      k.to(95, { x: k.now.x + each / 2, y: g.floor - g.u * 0.1, r: dir * 7, sy: 1.04 }, "out");
      k.to(95, { x: k.now.x + each / 2, y: g.floor, sy: 0.96 }, "in");
      bx += each;
      spin += (each / (2 * Math.PI * r)) * 360;
      k.propAt("ball", k.t, { x: bx, r: spin }, "out");
    }
    // The kick: a quick lean into the kick picture (or run2).
    const drawnBall = k.has("kick"); // the kick picture has the ball at its foot
    k.show("kick");
    if (drawnBall) k.propAt("ball", k.t, { o: 0 });
    k.to(110, { r: dir * (drawnBall ? 4 : 14), x: k.now.x + dir * g.u * 0.1, sy: 1 }, "in");
    if (drawnBall) {
      // Off it goes: the picture's ball becomes the flying one.
      const b = hand(k, g, "ball");
      bx = k.now.x + dir * Math.abs(b.x - k.now.x);
      k.propAt("ball", k.t, { x: bx, y: b.y, o: 1 });
      k.wait(90).show("standing");
    }
    const kick = k.t;
    const kickY = drawnBall ? hand(k, g, "ball").y : ballY;
    const target = { x: goal.x + dir * g.u * 0.15, y: g.floor - g.u * 0.55 };
    const flight = 560;
    for (let i = 1; i <= 8; i += 1) {
      const u = i / 8;
      k.propAt("ball", kick + flight * u, { x: bx + (target.x - bx) * u, y: kickY + (target.y - kickY) * u - g.u * 0.9 * 4 * u * (1 - u),
        r: spin + dir * 720 * u });
    }
    const inNet = kick + flight;
    k.propAt("ball", inNet + 380, { y: ballY, x: target.x + dir * g.u * 0.1 }, "in");
    // The net ripples and the goal is cheered.
    k.propAt("net", inNet, { sx: dir }).propAt("net", inNet + 120, { sx: dir * 1.2 }, "out")
      .propAt("net", inNet + 260, { sx: dir * 0.9 }, "soft").propAt("net", inNet + 400, { sx: dir * 1.06 }, "soft")
      .propAt("net", inNet + 520, { sx: dir }, "soft");
    k.fxAt(inNet + 60, "bubble", goal.x, g.floor - g.u * 1.5, { text: "GOAL!" });
    k.fxAt(inNet + 120, "sparkle", goal.x - dir * g.u * 0.3, g.floor - g.u * 1.3);
    k.fxAt(inNet + 300, "sparkle", goal.x + dir * g.u * 0.1, g.floor - g.u * 1.6);
    k.to(160, { r: 0, sy: 1 }, "out").show("standing");
    // A knee slide toward the goal (when there's a picture for it), then a
    // happy hop (the cheering picture, when there is one).
    if (k.has("slide")) {
      k.wait(Math.max(0, inNet - 200 - k.t)).show("slide", 80).to(420, { x: k.now.x + dir * g.u * 0.9 }, "out").fx("dust", -dir * g.u * 0.2, 0, { dir });
      k.wait(250).show("standing", 80);
    }
    k.wait(Math.max(0, inNet - k.t)).show("celebrate", 100).to(110, { sy: 0.9, sx: 1.06 }, "in").to(200, { y: g.floor - g.u * 0.3, sy: 1.06, sx: 0.96 }, "out")
      .to(200, { y: g.floor, sy: 0.92, sx: 1.05 }, "in").to(120, { sy: 1, sx: 1 }, "out");
    k.wait(Math.max(0, inNet + 1500 - k.t)).show("standing", 100);
    // Everything slides away.
    const away = k.t;
    for (const id of ["goal", "net"]) {
      k.propAt(id, away, { x: goal.x, o: 1 }).propAt(id, away + 450, { x: off.x, o: 0 }, "in");
    }
    k.propAt("ball", away, { o: 1 }).propAt("ball", away + 300, { o: 0 }, "out");
    sitDown(k);
    comeBack(k, g, start);
    if (k.pose !== REST) sitDown(k);
    return k.done("soccer");
  }

  // Tennis against the page's edge: a few rallies off the wall, then a smash.
  function tennis(g) {
    const k = track(g.pos, g.poses);
    const start = { ...g.pos };
    const room = roomOf(g);
    // Toward the nearer edge; with the tennis picture, toward the side its
    // racket is on (a picture is never mirrored).
    const dir = k.has("tennis") ? 1 : room.right <= room.left ? 1 : -1;
    const edge = edgeOf(g, dir);
    const spot = edge - dir * g.u * 4.2;
    const drawn = !k.has("tennis"); // the tennis picture has its own racket
    if (Math.abs(spot - k.now.x) > g.u * 0.5) { standUp(k, "walk1"); steps(k, g, spot, false); }
    standUp(k, drawn ? "tennis" : "standing");
    const t0 = k.t;
    const mid = (spot + edge) / 2;
    k.prop("court", "court").prop("tnet", "tennisnet").prop("ball", "tennisball");
    k.propAt("court", 0, { x: spot, y: g.floor, o: 0, sx: (edge - spot) / g.u }).propAt("court", t0, { o: 0 }).propAt("court", t0 + 300, { o: 1 });
    k.propAt("tnet", 0, { x: mid, y: g.floor, o: 0, sy: 0.2 }).propAt("tnet", t0, { o: 0, sy: 0.2 }).propAt("tnet", t0 + 320, { o: 1, sy: 1 }, "pop");
    const which = dir > 0 ? "near" : "far";
    const grip = () => { const set = HANDS[k.pose] || HANDS.rest; return set[which] || set.near; };
    if (drawn) {
      k.prop("racket", "racket", "body");
      const gp = grip();
      k.propAt("racket", 0, { x: gp[0] * g.u, y: gp[1] * g.u, o: 0, r: -dir * 40, sx: dir }).propAt("racket", t0, { o: 1 });
    }
    const contact = () => (drawn ? { x: k.now.x + dir * g.u * 0.75, y: g.floor - g.u * 0.75 }
      : { x: k.now.x + HANDS.tennis.ball[0] * g.u, y: k.now.y + HANDS.tennis.ball[1] * g.u });
    const wall = { x: edge - dir * g.u * 0.08, y: g.floor - g.u * 0.55 };
    const bounceAt = { x: spot + dir * g.u * 1.4, y: g.floor - g.u * 0.06 };
    let c = contact();
    k.propAt("ball", t0, { x: c.x, y: c.y - g.u * 0.5, o: 0 }).propAt("ball", t0 + 200, { o: 1 });
    k.wait(420);
    // A toss, then the rallies.
    k.propAt("ball", k.t - 200, { x: c.x, y: c.y - g.u * 0.6 }, "out").propAt("ball", k.t, { x: c.x, y: c.y }, "in");
    for (let i = 0; i < 3; i += 1) {
      const hit = k.t;
      if (drawn) k.propAt("racket", hit - 160, { r: -dir * 70 }, "soft").propAt("racket", hit, { r: dir * 45 }, "in").propAt("racket", hit + 240, { r: -dir * 30 }, "out");
      // The swing: the tennis picture just for that moment (its own ball sits
      // where ours arrives, beside the racket).
      if (!drawn) k.show("tennis");
      k.to(120, { r: dir * 6 }, "in").to(200, { r: 0 }, "out");
      if (!drawn) k.show("standing");
      c = contact();
      const over = g.floor - g.u * 1.25;
      k.propAt("ball", hit + 260, { x: mid, y: over }, "out").propAt("ball", hit + 520, { x: wall.x, y: wall.y }, "in");
      k.fxAt(hit + 520, "sparkle", wall.x, wall.y);
      k.propAt("ball", hit + 820, { x: bounceAt.x, y: bounceAt.y }, "in");
      k.propAt("ball", hit + 1120, { x: c.x, y: c.y }, "out");
      k.wait(Math.max(0, hit + 1120 - k.t));
    }
    // The smash: a high ball, the raised hand, straight into the wall.
    const toss = k.t;
    k.propAt("ball", toss + 380, { x: c.x - dir * g.u * 0.2, y: g.floor - g.u * 1.5 }, "out");
    k.show(drawn ? "wave" : "standing", 100);
    if (drawn) {
      const up = (HANDS[k.pose] || HANDS.rest).up || HANDS.rest.up;
      k.propAt("racket", toss + 100, { x: up[0] * g.u, y: up[1] * g.u, r: -dir * 120 }, "soft");
    }
    k.to(360, { r: -dir * 6, y: g.floor - g.u * 0.1 }, "out");
    const smash = k.t;
    if (drawn) k.propAt("racket", smash + 120, { r: dir * 60 }, "in");
    else k.show("tennis");
    k.to(140, { r: dir * 10, y: g.floor }, "in");
    k.propAt("ball", smash + 300, { x: wall.x, y: g.floor - g.u * 0.3 }, "in");
    k.fxAt(smash + 300, "sparkle", wall.x, g.floor - g.u * 0.3).fxAt(smash + 380, "sparkle", wall.x - dir * g.u * 0.2, g.floor - g.u * 0.8);
    k.propAt("ball", smash + 750, { x: wall.x - dir * g.u * 2.2, y: g.floor - g.u * 2.4, o: 0 }, "out");
    k.wait(300).to(200, { r: 0 }, "soft");
    if (!drawn) k.show("standing", 100);
    // Everything fades.
    const fade = Math.max(k.t, smash + 600);
    for (const id of drawn ? ["court", "tnet", "racket"] : ["court", "tnet"]) {
      k.propAt(id, fade, {}).propAt(id, fade + 400, { o: 0 }, "out");
    }
    k.wait(Math.max(0, fade + 400 - k.t));
    sitDown(k);
    comeBack(k, g, start);
    if (k.pose !== REST) sitDown(k);
    return k.done("tennis");
  }

  // ---------------------------------------------------------------- moves for the new poses

  // A cheer: a jump with both arms up, and sparkles.
  function celebrate(g) {
    const k = track(g.pos, g.poses);
    const y = g.pos.y;
    standUp(k, "celebrate");
    k.show("celebrate");
    k.to(110, { sy: 0.88, sx: 1.07 }, "in").to(240, { y: y - g.u * 0.4, sy: 1.06, sx: 0.96 }, "out")
      .fx("sparkle", -g.u * 0.5, -g.u * 1.4).fx("sparkle", g.u * 0.55, -g.u * 1.5);
    k.to(220, { y, sy: 0.9, sx: 1.06 }, "in").fx("sparkle", 0, -g.u * 1.8).to(140, { sy: 1, sx: 1 }, "out");
    k.to(110, { sy: 0.92 }, "in").to(200, { y: y - g.u * 0.2, sy: 1.04 }, "out").to(200, { y, sy: 1 }, "in").wait(300);
    sitDown(k);
    return k.done("celebrate");
  }

  // A knee-slide entrance: out a little way, a dash back and a slide into place.
  function kneeSlide(g) {
    const k = track(g.pos, g.poses);
    const start = { ...g.pos };
    const room = roomOf(g);
    const dir = room.right >= room.left ? 1 : -1;
    const out = clamp(start.x + dir * g.u * 1.6, g.left, g.right);
    standUp(k, "walk1");
    steps(k, g, out, false);
    k.wait(200);
    // Back toward home, then the knee slide for the last stretch.
    const slideFrom = start.x + (out - start.x) * 0.55;
    steps(k, g, slideFrom, true);
    k.show(k.has("slide") ? "slide" : "standing", 80);
    k.to(520, { x: start.x, r: 0, sy: 1 }, "out").fx("dust", dir * g.u * 0.25, 0, { dir: -dir }).fx("dust", dir * g.u * 0.1, 0, { dir: -dir });
    k.wait(350).fx("sparkle", 0, -g.u * 1.2);
    if (k.has("celebrate")) k.show("celebrate", 100).to(160, { y: start.y - g.u * 0.15 }, "out").to(160, { y: start.y }, "in");
    k.show("standing", 100).to(120, { r: 0 });
    sitDown(k);
    return k.done("slide");
  }

  // Pointing up (toward the menu bar) when it has something to say.
  function point(g, say) {
    const k = track(g.pos, g.poses);
    const y = g.pos.y;
    if (!k.has("point")) {
      k.fx("bubble", 0, -g.u * 1.1, { text: say || "💡" }).head(300, -8, 0).wait(1400).head(300, 0, 0).wait(300);
      return k.done("point");
    }
    standUp(k, "point");
    k.show("point").to(160, { y: y - g.u * 0.12, sy: 1.04 }, "out").to(160, { y, sy: 1 }, "in")
      .fx("bubble", g.u * 0.35, -g.u * 1.75, { text: say || "💡" }).wait(1500);
    sitDown(k);
    return k.done("point");
  }

  // Charging an energy ball: it glows, sparkles burst, and it fades.
  function power(g) {
    const k = track(g.pos, g.poses);
    standUp(k, "power");
    k.show("power");
    const b = hand(k, g, "ball");
    const orb = k.has("power") ? b : { x: k.now.x, y: k.now.y - g.u * 1.2 };
    k.fxAt(k.t, "orb", orb.x, orb.y);
    k.to(500, { sy: 0.97, sx: 1.02 }, "soft").to(500, { sy: 1, sx: 1 }, "soft");
    for (const [dx, dy] of [[-0.3, -0.2], [0.25, -0.35], [0.05, 0.3], [-0.2, 0.25]]) k.fxAt(k.t + 60, "sparkle", orb.x + dx * g.u, orb.y + dy * g.u);
    k.to(120, { sy: 0.92 }, "in").to(200, { sy: 1 }, "out").wait(500);
    sitDown(k);
    return k.done("power");
  }

  // Hearts (rare): when the user says thanks, or comes back after a long time.
  function love(g) {
    const k = track(g.pos, g.poses);
    if (!k.has("love")) {
      k.fx("bubble", 0, -g.u * 1.1, { text: "💙" }).head(360, 10, 0).wait(900).head(360, 0, 0).wait(400);
      return k.done("love");
    }
    standUp(k, "love");
    k.show("love");
    for (let i = 0; i < 2; i += 1) k.to(420, { r: 3, y: g.pos.y - g.u * 0.04 }, "soft").to(420, { r: -2, y: g.pos.y }, "soft");
    k.fx("sparkle", g.u * 0.5, -g.u * 1.5).to(200, { r: 0 }, "soft").wait(300);
    sitDown(k);
    return k.done("love");
  }

  // Waiting (rare): arms crossed with a little foot tap and a "…", when the
  // chat is open and nothing has happened for a while. Playful, not grumpy.
  function waiting(g) {
    const k = track(g.pos, g.poses);
    if (!k.has("arms-crossed")) {
      k.fx("bubble", 0, -g.u * 1.1, { text: "…" }).head(300, 6, 0).wait(1300).head(300, 0, 0).wait(300);
      return k.done("wait");
    }
    standUp(k, "arms-crossed");
    k.show("arms-crossed").fx("bubble", 0, -g.u * 1.7, { text: "…" });
    for (let i = 0; i < 3; i += 1) k.to(140, { y: g.pos.y - g.u * 0.03, r: 1.5 }, "out").to(160, { y: g.pos.y, r: 0 }, "in").wait(220);
    k.to(240, { r: -3 }, "soft").wait(500).to(200, { r: 0 }, "soft");
    sitDown(k);
    return k.done("wait");
  }

  // ---------------------------------------------------------------- choosing where

  // g: {pos, home, floor, left, right, top, u, poses, random, profile, avoid}
  function rnd(g) { return typeof g.random === "function" ? g.random() : Math.random(); }

  function between(g, a, b) { return a + (b - a) * rnd(g); }

  function plan(name, g, opt) {
    const o = opt || {};
    const span = g.right - g.left;
    const ok = (x) => !g.avoid || x < g.avoid.left - g.u || x > g.avoid.right + g.u;
    const spot = (minFrac) => {
      for (let i = 0; i < 6; i += 1) {
        const dir = rnd(g) < 0.5 ? -1 : 1;
        const x = clamp(g.pos.x + dir * between(g, minFrac, minFrac * 2.2) * span, g.left, g.right);
        if (ok(x) && Math.abs(x - g.pos.x) > g.u * 0.8) return x;
      }
      return null;
    };
    switch (name) {
      case "walk": case "run": {
        if (!atFloor(g)) return null;
        const x = spot(name === "run" ? 0.25 : 0.12);
        return x === null ? null : walk(g, x, name === "run");
      }
      case "stroll": {
        if (!atFloor(g)) return null;
        const inward = g.pos.x - g.left > g.right - g.pos.x ? -1 : 1;
        return stroll(g, g.pos.x + inward * between(g, 0.18, 0.4) * span);
      }
      case "fly": {
        if (g.profile === "web") return fly(g, { x: g.pos.x + between(g, -0.45, 0.45) * span, y: g.top + (g.floor - g.top) * between(g, 0.15, 0.5) }, true);
        const floor = rnd(g) < 0.55;
        for (let i = 0; i < 6; i += 1) {
          const x = between(g, g.left, g.right);
          const y = floor ? g.floor : between(g, g.top + g.u * 1.6, g.floor);
          if (ok(x) && Math.hypot(x - g.pos.x, y - g.pos.y) > g.u * 2) return fly(g, { x, y }, false);
        }
        return null;
      }
      case "climb": {
        if (!atFloor(g)) return null;
        const side = o.side || (g.pos.x - g.left < g.right - g.pos.x ? -1 : 1);
        if (g.profile === "web" && Math.abs((side < 0 ? g.left : g.right) - g.pos.x) > span * 0.5) return null;
        return climb(g, side);
      }
      case "trick": return plan(TRICKS[Math.floor(rnd(g) * TRICKS.length) % TRICKS.length], g, o);
      case "sport": {
        const list = g.profile === "web" && g.energy !== "playful" ? ["football"] : SPORTS;
        return plan(list[Math.floor(rnd(g) * list.length) % list.length], g, o);
      }
      case "football": return football(g);
      case "soccer": return soccer(g);
      case "tennis": return tennis(g);
      case "celebrate": return celebrate(g);
      case "slide": return atFloor(g) ? kneeSlide(g) : celebrate(g);
      case "point": return point(g, o.say);
      case "power": return power(g);
      case "love": return love(g);
      case "wait": return waiting(g);
      case "land": return fly(g, { x: g.pos.x + (g.pos.x - g.left > g.right - g.pos.x ? -1 : 1) * g.u * 3, y: g.floor }, false);
      case "backflip": return backflip(g);
      case "spin": return spin(g);
      case "bounce": return bounce(g);
      case "peek": return peek(g);
      case "wave": return wave(g, o.how);
      case "look": return look(g, o.at || g.pointer);
      case "nod": return nod(g);
      case "home": return home(g, g.home);
      default: return null;
    }
  }

  // ---------------------------------------------------------------- choosing when

  // Calm mode: the user's choice, or (when they haven't chosen) "reduce motion".
  function isCalm(ctx) {
    const c = ctx || {};
    if (c.calm === true || c.calm === false) return c.calm;
    return Boolean(c.reducedMotion);
  }

  // Why the robot must keep still now, or "" if it may wander.
  function stillReason(ctx) {
    const c = ctx || {};
    if (c.hidden) return "hidden";
    if (c.dragging) return "dragging";
    if (c.busy) return "busy";
    if (c.profile === "web" && c.reducedMotion) return "reduced-motion";
    if (isCalm(c)) return c.calm === true ? "calm" : "reduced-motion";
    if (c.chatOpen) return "chat";
    if (c.typing) return "typing";
    if (c.fullscreen) return "fullscreen";
    return "";
  }

  function energyOf(ctx) { return ENERGY.includes(ctx && ctx.energy) ? ctx.energy : "normal"; }

  // Milliseconds until the robot next thinks about doing something.
  function nextDelay(ctx, random) {
    const r = typeof random === "function" ? random : Math.random;
    const [lo, hi] = GAPS[(ctx && ctx.profile) === "web" ? "web" : "desktop"][energyOf(ctx)];
    return Math.round((lo + (hi - lo) * r()) * 1000);
  }

  // The next move's name, or null to keep resting.
  function pick(ctx, random) {
    const c = ctx || {};
    if (stillReason(c)) return null;
    const r = typeof random === "function" ? random : Math.random;
    const weights = { ...WEIGHTS[c.profile === "web" ? "web" : "desktop"][energyOf(c)] };
    if (!c.away) delete weights.home;
    if (c.away && weights.home) weights.home *= 1.5;
    if (c.atFloor === false) { delete weights.walk; delete weights.run; delete weights.climb; delete weights.stroll; }
    const names = Object.keys(weights);
    const total = names.reduce((s, n) => s + weights[n], 0);
    let roll = r() * total;
    for (const n of names) { roll -= weights[n]; if (roll < 0) return n; }
    return names[names.length - 1] || null;
  }

  // A reaction to something the user did ("return": back after being away;
  // "chat": the chat opened; "hello": the robot first appears). Returns how
  // to wave ("wave", "gentle" or "still"), or null to keep still.
  // A cheer for something the user did: "done" (an action they allowed
  // finished): celebrate; "thanks" (they said thanks in the chat) or
  // "missed" (back after a long time): love. Returns the move, or null.
  function cheer(kind, ctx) {
    const c = ctx || {};
    if (c.hidden || c.dragging || c.busy || c.typing || c.fullscreen || c.reducedMotion || c.calm === true) return null;
    return { done: "celebrate", thanks: "love", missed: "love" }[kind] || null;
  }

  function reaction(kind, ctx) {
    const c = ctx || {};
    if (!["return", "chat", "hello"].includes(kind)) return null;
    if (c.hidden || c.dragging || c.busy || c.typing || c.fullscreen) return null;
    if (c.reducedMotion) return "still";
    if (isCalm(c) || kind === "chat") return "gentle";
    return "wave";
  }

  // Is a point over the robot (its box, plus a little margin)?
  function hit(rect, point, pad) {
    if (!rect || !point) return false;
    const p = pad || 0;
    return point.x >= rect.left - p && point.x <= rect.right + p && point.y >= rect.top - p && point.y <= rect.bottom + p;
  }

  const api = {
    VERSION, REST, POSES, FALLBACK, EASE, ENERGY, GAPS, WEIGHTS, TRICKS,
    SPORTS, HANDS, cheer, resolvePose, posesFromFiles, plan, walk, stroll, fly, climb, backflip, spin, bounce, peek, wave, look, nod, home,
    football, soccer, tennis, hand, celebrate, kneeSlide, point, power, love, waiting,
    isCalm, stillReason, nextDelay, pick, reaction, hit, atFloor,
  };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PilotMotion = api;
})(typeof window !== "undefined" ? window : globalThis);
