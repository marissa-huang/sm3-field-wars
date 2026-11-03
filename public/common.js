/* Shared helpers for every SM3 Field Wars page: live sync, API calls, timer math,
   the field chart, and the allocation entry form. */
const SMT = (() => {
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* private mode */ } },
  };

  let pin = store.get("smt-pin") || "";
  let state = null;
  let offset = 0;          // serverNow - Date.now()
  let fetching = null;
  let publicOnly = false;  // the projector never sends the PIN, so it can only ever get the public view
  const listeners = [];

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function refresh() {
    if (fetching) return fetching;
    fetching = (async () => {
      try {
        const r = await fetch("/api/state", { headers: pin && !publicOnly ? { "X-Pin": pin } : {} });
        const s = await r.json();
        offset = s.serverNow - Date.now();
        state = s;
        listeners.forEach((f) => f(s));
      } finally {
        fetching = null;
      }
    })();
    return fetching;
  }

  function setLive(on) {
    document.querySelectorAll(".live-dot").forEach((el) => {
      el.classList.toggle("off", !on);
      el.textContent = on ? "Live" : "Reconnecting…";
    });
  }

  /* Poll a tiny version number instead of holding a connection open. This works on any
     network, survives phones sleeping, and never runs into the browser's limit of 6 open
     connections per site when organizers have several tabs open. */
  function connect(onState, opts = {}) {
    publicOnly = !!opts.publicOnly;
    if (onState) listeners.push(onState);
    const poll = async () => {
      try {
        const r = await fetch("/api/version", { cache: "no-store" });
        const { version } = await r.json();
        setLive(true);
        if (!state || version !== state.version) await refresh();
      } catch (e) {
        setLive(false);
      }
    };
    setInterval(poll, 1500);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
    return refresh();
  }

  async function act(type, payload = {}) {
    const r = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Pin": pin },
      body: JSON.stringify({ type, payload }),
    });
    const out = await r.json().catch(() => ({ ok: false, error: "Server error" }));
    if (!r.ok || out.ok === false) throw new Error(out.error || "Something went wrong");
    await refresh().catch(() => {});  // show our own change immediately
    return out;
  }

  /* Ask for the PIN, verify it against the server, then call onOk(). */
  function gate(root, title, onOk) {
    const tryPin = async (p) => {
      pin = p;
      const r = await fetch("/api/state", { headers: { "X-Pin": pin } });
      const s = await r.json();
      if (s.role === "admin") { store.set("smt-pin", pin); onOk(); return true; }
      return false;
    };
    const show = (msg) => {
      root.innerHTML = `
        <div class="login"><div class="card">
          <h1>${esc(title)}</h1>
          <div class="muted">Enter the organizer PIN to continue.</div>
          <form><input type="password" autocomplete="current-password" placeholder="PIN" autofocus>
          <button class="primary big">Continue</button></form>
          <div class="err" style="color:var(--cardinal);margin-top:8px;font-weight:600">${esc(msg || "")}</div>
        </div></div>`;
      root.querySelector("form").onsubmit = async (e) => {
        e.preventDefault();
        if (!(await tryPin(root.querySelector("input").value.trim()))) show("That PIN didn't work.");
      };
    };
    if (pin) tryPin(pin).then((ok) => ok || show());
    else show();
  }

  function logout() { store.del("smt-pin"); pin = ""; location.reload(); }

  function now() { return Date.now() + offset; }

  function timeLeft(t) {
    if (!t) return 0;
    let rem = t.remaining;
    if (t.startedAt) rem -= (now() - t.startedAt) / 1000;
    return Math.max(0, rem);
  }

  function clock(sec) {
    sec = Math.ceil(sec);
    return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  }

  function toast(msg, kind = "") {
    let box = document.querySelector(".toast-box");
    if (!box) { box = document.createElement("div"); box.className = "toast-box"; document.body.appendChild(box); }
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    box.appendChild(el);
    setTimeout(() => el.remove(), kind === "err" ? 4500 : 2600);
  }

  /* What players are allowed to know about each field before results. Volcano, Unstable
     Terrain, a secret Hidden Treasure and Frozen Fortress's thawed field are only known after
     scoring, so those fields come back with maybe: true. Host screens use this too, so a team
     peeking at a host's phone learns nothing extra. */
  function knownFields(round) {
    const ev = new Set(round.events || []);
    const d = round.draws || {};
    const thaw = ev.has("frozen") && round.revealed ? d.thaw : null;
    const treasure = ev.has("crown") && (round.revealed || !round.crownHidden) ? d.crown : null;
    const anyField = ev.has("volcano") || ev.has("unstable") || (ev.has("crown") && !treasure);
    const out = [];
    for (let i = 0; i < 5; i++) {
      let v = round.mode === "weighted" ? i + 1 : 1;
      let maybe = anyField;
      const tags = [];
      if (ev.has("frozen")) {
        if (i === 2) { v = 0; maybe = false; tags.push("🧊 frozen"); }
        else if (thaw === i + 1) { v *= 2; tags.push("🔥 ×2"); }
        else if (!thaw && (i === 1 || i === 3)) { maybe = true; tags.push("🔥 ×2?"); }
      }
      if (treasure === i + 1 && v) { v *= 2; tags.push("💎 ×2"); }
      out.push({ value: v, tags, maybe });
    }
    return out;
  }

  /* One-line reminders of the event rules that limit what a team may write down. */
  function entryRules(round) {
    const ev = round.events || [];
    const out = [];
    if (ev.includes("nomans")) out.push("🚫 No Man's Land: exactly one field must be 0");
    if (ev.includes("staircase")) out.push("🪜 Staircase: Field 1 ≤ Field 2 ≤ Field 3 ≤ Field 4 ≤ Field 5");
    if (ev.includes("sabotage")) out.push("💣 Sabotage: also pick one field to sabotage");
    return out;
  }

  function ruleProblem(round, vals) {
    const ev = round.events || [];
    if (ev.includes("nomans") && vals.filter((v) => v === 0).length !== 1) return "No Man's Land: exactly one field must be 0 (the other four need at least 1 soldier).";
    if (ev.includes("staircase") && vals.some((v, i) => i < 4 && v > vals[i + 1])) return "Staircase: each field needs at least as many soldiers as the one before it.";
    return "";
  }

  /* Allocation that actually fought after Random Teleportation swapped two fields. */
  function teleported(alloc, swap) {
    const out = [...alloc];
    if (swap) [out[swap[0] - 1], out[swap[1] - 1]] = [out[swap[1] - 1], out[swap[0] - 1]];
    return out;
  }

  function fieldNoteIcons(notes) {
    const map = { crown: "💎", volcano: "🌋", unstable: "⛰️", frozen: "🧊", thaw: "🔥" };
    return (notes || []).map((n) => map[n] || n).join(" ");
  }

  /* Grouped bar chart of mean + median soldiers per field. Returns an SVG string. */
  function fieldChart(stats, opts = {}) {
    const W = opts.width || 640, H = opts.height || 340;
    const fs = opts.fontSize || 14;
    // Legend sits in its own band, with headroom above the tallest bar for its value label.
    const pad = { l: 44, r: 12, t: fs * 3 + 8, b: opts.values ? fs * 3 + 16 : fs * 2 + 14 };
    const iw = W - pad.l - pad.r, ih = H - pad.t - pad.b;
    const peak = Math.max(1, ...stats.map((s) => Math.max(s.mean, s.median)));
    const step = peak > 50 ? 20 : peak > 20 ? 10 : 5;
    const top = Math.ceil((peak * 1.08) / step) * step;
    const y = (v) => pad.t + ih - (v / top) * ih;
    const gw = iw / stats.length, bw = Math.min(46, gw * 0.32);
    let g = "";
    for (let v = 0; v <= top; v += step) {
      g += `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)" stroke-width="1"/>`;
      g += `<text x="${pad.l - 8}" y="${y(v) + 4}" text-anchor="end" font-size="${fs - 2}" fill="var(--muted)">${v}</text>`;
    }
    stats.forEach((s, i) => {
      const cx = pad.l + gw * i + gw / 2;
      const bars = [[s.mean, "var(--cardinal)", cx - bw - 2], [s.median, "var(--sand-dark)", cx + 2]];
      bars.forEach(([v, c, x]) => {
        const label = Number.isInteger(v) ? v : v.toFixed(1);
        g += `<rect x="${x}" y="${y(v)}" width="${bw}" height="${Math.max(0, pad.t + ih - y(v))}" rx="3" fill="${c}"><title>${label}</title></rect>`;
        g += `<text x="${x + bw / 2}" y="${y(v) - fs * 0.5}" text-anchor="middle" font-size="${fs - 1}" font-weight="700" fill="var(--ink)">${label}</text>`;
      });
      g += `<text x="${cx}" y="${H - pad.b + fs * 1.6}" text-anchor="middle" font-size="${fs}" font-weight="700" fill="var(--ink)">Field ${s.field}</text>`;
      if (opts.values) {
        const v = opts.values[i], tag = fieldNoteIcons(opts.notes && opts.notes[String(i + 1)]);
        g += `<text x="${cx}" y="${H - pad.b + fs * 2.9}" text-anchor="middle" font-size="${fs - 2}" fill="var(--muted)">worth ${v} ${esc(tag)}</text>`;
      }
    });
    const sw = fs * 0.85, gap = fs * 5.5;
    const legend = `<g font-size="${fs}" font-weight="600" fill="var(--ink)">
      <rect x="${pad.l}" y="4" width="${sw}" height="${sw}" rx="2" fill="var(--cardinal)"/><text x="${pad.l + sw + 6}" y="${4 + sw}">Mean</text>
      <rect x="${pad.l + gap}" y="4" width="${sw}" height="${sw}" rx="2" fill="var(--sand-dark)"/><text x="${pad.l + gap + sw + 6}" y="${4 + sw}">Median</text></g>`;
    return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Mean and median soldiers per field" style="display:block;font-family:var(--font)">${legend}${g}</svg>`;
  }

  function miniBars(alloc, budget, capital) {
    return `<span class="mini" title="${alloc.join(" · ")}">${alloc.map((v, i) =>
      `<i class="${capital === i + 1 ? "cap" : ""}" style="height:${Math.max(1, (v / budget) * 22)}px"></i>`).join("")}</span>`;
  }

  /* Allocation form. opts: {round, team, existing, enteredBy, admin, onDone, onCancel} */
  function allocForm(root, opts) {
    const { round, team } = opts;
    const budget = round.budget;
    const isCap = round.mode === "capital";
    const isSab = (round.events || []).includes("sabotage");
    const known = knownFields(round);
    const rules = entryRules(round);
    let capital = opts.existing ? opts.existing.capital : null;
    let sabotage = opts.existing ? opts.existing.sabotage : null;
    const start = opts.existing ? opts.existing.alloc : ["", "", "", "", ""];

    root.innerHTML = `
      <div class="alloc">
        <div class="alloc-head"><h3>${esc(team.name)}</h3><span class="muted">Round ${round.number} · ${budget} soldiers</span></div>
        ${rules.length ? `<div class="alloc-rules">${rules.map((x) => `<div>${esc(x)}</div>`).join("")}</div>` : ""}
        <div class="alloc-fields">
          ${known.map((k, i) => `
            <label class="fld" data-i="${i}">
              <span class="fl">Field ${i + 1}</span>
              <span class="fv">${round.mode === "weighted" || k.tags.length ? `${k.value}${k.maybe ? "?" : ""} pt${k.value === 1 ? "" : "s"} ${esc(k.tags.join(" "))}` : "&nbsp;"}</span>
              <input type="number" inputmode="numeric" min="0" max="${budget}" step="1" value="${start[i]}" aria-label="Field ${i + 1}">
            </label>`).join("")}
        </div>
        ${isCap ? `<div class="cap-pick"><span class="lbl">🏰 Capital:</span>${[1, 2, 3, 4, 5].map((n) => `<button type="button" data-cap="${n}">Field ${n}</button>`).join("")}</div>` : ""}
        ${isSab ? `<div class="cap-pick sab-pick"><span class="lbl">💣 Sabotage:</span>${[1, 2, 3, 4, 5].map((n) => `<button type="button" data-sab="${n}">Field ${n}</button>`).join("")}</div>` : ""}
        <div class="sumbar"><div class="fill"></div><div class="txt"></div></div>
        <div class="err"></div>
        <div class="slot"></div>
        <div class="actions">
          ${opts.onCancel ? `<button type="button" class="cancel">Cancel</button>` : ""}
          <button type="button" class="primary save">Save allocation</button>
        </div>
      </div>`;

    const inputs = [...root.querySelectorAll(".fld input")];
    const save = root.querySelector(".save");
    const err = root.querySelector(".err");
    const slot = root.querySelector(".slot");

    function read() { return inputs.map((el) => (el.value === "" ? NaN : Number(el.value))); }

    function check() {
      const vals = read();
      const filled = vals.map((v) => (Number.isFinite(v) ? v : 0));
      const sum = filled.reduce((a, b) => a + b, 0);
      const bar = root.querySelector(".sumbar");
      bar.querySelector(".fill").style.width = `${Math.min(100, (sum / budget) * 100)}%`;
      bar.classList.toggle("ok", sum === budget);
      bar.classList.toggle("over", sum > budget);
      const diff = budget - sum;
      bar.querySelector(".txt").textContent = `${sum} / ${budget}  ·  ` +
        (diff === 0 ? "✓ exactly right" : diff > 0 ? `${diff} left to place` : `${-diff} too many`);
      root.querySelectorAll(".fld").forEach((f, i) => f.classList.toggle("capital", capital === i + 1));
      root.querySelectorAll("[data-cap]").forEach((b) => b.classList.toggle("on", Number(b.dataset.cap) === capital));
      root.querySelectorAll("[data-sab]").forEach((b) => b.classList.toggle("on", Number(b.dataset.sab) === sabotage));
      let problem = "";
      if (vals.some((v) => Number.isFinite(v) && (!Number.isInteger(v) || v < 0 || v > budget))) problem = `Each field must be a whole number from 0 to ${budget}.`;
      else if (vals.some((v) => !Number.isFinite(v))) problem = "";
      else if (sum !== budget) problem = "";
      else if (ruleProblem(round, vals)) problem = ruleProblem(round, vals);
      else if (isCap && !capital) problem = "Pick which field is the capital.";
      else if (isSab && !sabotage) problem = "Pick which field to sabotage.";
      const ready = !problem && vals.every(Number.isFinite) && sum === budget && (!isCap || capital) && (!isSab || sabotage);
      err.textContent = problem;
      save.disabled = !ready;
      return ready;
    }

    async function submit(overwrite) {
      if (!check()) return;
      save.disabled = true;
      try {
        const out = await act("submit", {
          roundId: round.id, teamId: team.id, alloc: read(), capital, sabotage,
          enteredBy: opts.enteredBy || "", overwrite: overwrite || !!opts.admin, force: !!opts.admin,
        });
        if (out.conflict) {
          const ex = out.existing;
          slot.innerHTML = `<div class="conflict"><b>${esc(team.name)} already has an entry</b>
            ${ex.enteredBy ? ` from ${esc(ex.enteredBy)}` : ""}: <b class="num">${ex.alloc.join(" · ")}</b>${ex.capital ? ` (capital ${ex.capital})` : ""}${ex.sabotage ? ` (sabotage ${ex.sabotage})` : ""}.
            <div class="row" style="margin-top:8px"><button type="button" class="primary ow">Replace with my numbers</button>
            <button type="button" class="keep">Keep existing</button></div></div>`;
          slot.querySelector(".ow").onclick = () => { slot.innerHTML = ""; submit(true); };
          slot.querySelector(".keep").onclick = () => { slot.innerHTML = ""; opts.onDone && opts.onDone(false); };
          save.disabled = false;
          return;
        }
        toast(out.same ? `${team.name}: already saved (same numbers)` : `Saved ${team.name}`, "ok");
        opts.onDone && opts.onDone(true);
      } catch (e) {
        err.textContent = e.message;
        save.disabled = false;
      }
    }

    inputs.forEach((el, i) => {
      el.addEventListener("input", check);
      el.addEventListener("focus", () => el.select());
      el.addEventListener("keydown", (e) => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        if (i < inputs.length - 1) inputs[i + 1].focus();
        else submit(false);
      });
    });
    root.querySelectorAll("[data-cap]").forEach((b) => (b.onclick = () => { capital = Number(b.dataset.cap); check(); }));
    root.querySelectorAll("[data-sab]").forEach((b) => (b.onclick = () => { sabotage = Number(b.dataset.sab); check(); }));
    save.onclick = () => submit(false);
    if (opts.onCancel) root.querySelector(".cancel").onclick = opts.onCancel;
    check();
    setTimeout(() => inputs[0].focus(), 30);
  }

  return {
    esc, store, connect, refresh, act, gate, logout, now, timeLeft, clock, toast,
    knownFields, entryRules, teleported, fieldNoteIcons, fieldChart, miniBars, allocForm,
    get state() { return state; },
  };
})();
