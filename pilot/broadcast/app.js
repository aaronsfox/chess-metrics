/* Chess match viewer prototype.
 *
 * Reads a feed produced by `chessmetrics feed` (one frame per position) from
 * window.FEED (feed.js) or feed.json. The board is always visible with the
 * key summary (players, clocks, decision difficulty, win probability) around
 * it; everything else lives in the tabs on the right. Nothing is drawn on the
 * board automatically except the last move: viewers draw their own arrows and
 * circles, and switch metric overlays on from the Position tab.
 *
 * A live service would push frames over a WebSocket; `ChessViewer.showFrame`
 * is the only entry point it needs.
 */
(function () {
  "use strict";

  const SVGNS = "http://www.w3.org/2000/svg";
  const SQ = 100; // board units per square

  // ---------- piece set (100x100, drawn in-house) ----------
  const BASE = '<rect x="24" y="80" width="52" height="10" rx="4"/>';
  const PIECES = {
    p: { body: '<circle cx="50" cy="30" r="13"/><path d="M34 80 Q36 56 44 46 L56 46 Q64 56 66 80 Z"/>' + BASE },
    r: { body: '<path d="M28 18 h10 v8 h7 v-8 h10 v8 h7 v-8 h10 v20 h-44 z"/><rect x="33" y="38" width="34" height="42"/>' + BASE,
         detail: '<path d="M33 44 h34"/>' },
    n: { body: '<path d="M30 80 Q27 70 32 62 Q38 55 41 48 Q38 45 34 48 L27 53 Q20 54 18 47 L20 39 Q24 27 33 21 L38 11 L43 20 Q60 23 67 40 Q74 58 70 80 Z"/>' + BASE,
         detail: '<circle cx="37" cy="31" r="1.5"/>' },
    b: { body: '<circle cx="50" cy="13" r="6"/><path d="M36 80 Q33 60 42 50 Q30 40 50 20 Q70 40 58 50 Q67 60 64 80 Z"/>' + BASE,
         detail: '<path d="M55 30 L46 42"/>' },
    q: { body: '<path d="M32 80 L26 34 L40 54 L44 26 L50 50 L56 26 L60 54 L74 34 L68 80 Z"/>' +
               '<circle cx="26" cy="30" r="5"/><circle cx="44" cy="22" r="5"/><circle cx="56" cy="22" r="5"/><circle cx="74" cy="30" r="5"/>' + BASE },
    k: { body: '<path d="M34 80 Q29 58 36 49 Q42 41 50 43 Q58 41 64 49 Q71 58 66 80 Z"/><rect x="46" y="10" width="8" height="30" rx="2"/><rect x="37" y="18" width="26" height="8" rx="2"/>' + BASE },
  };
  const MARKS = [1, 2, 3]; // colour slots; the scheme's --mark-N tokens give the colours
  const SCHEMES = ["broadcast", "scoreboard", "track", "matchday"];
  const KIND_LABEL = { swing: "Swing", difficulty: "Difficulty", overload: "Overload", conversion: "Conversion" };

  // ---------- state ----------
  const state = {
    feed: null, k: 0, playing: false, timer: null, tab: "game",
    layers: { overload: false, control: false, network: false },
    drawMode: false, mark: 1, marks: {}, drag: null, videoUrl: null,
  };

  // ---------- helpers ----------
  const $ = (sel) => document.querySelector(sel);
  const svg = (tag, attrs = {}) => {
    const n = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const sqXY = (name) => {
    const f = name.charCodeAt(0) - 97, r = Number(name[1]) - 1;
    return { x: f * SQ, y: (7 - r) * SQ, cx: f * SQ + SQ / 2, cy: (7 - r) * SQ + SQ / 2 };
  };
  const pct = (x) => (x < 0.005 ? "<1" : String(Math.round(x * 100)));
  const surname = (n) => (n.split(",")[0] || n).trim();
  const fmtClock = (s) => {
    if (s == null) return "–";
    s = Math.max(0, Math.round(s));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    return h ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}` : `${m}:${String(sec).padStart(2, "0")}`;
  };
  const expWhite = (wdl) => wdl[0] + 0.5 * wdl[1];
  const humanWdl = (f) => (f.human ? f.human.wdlSmooth || f.human.wdl : f.engine.wdl);
  const parseFen = (fen) => {
    const out = [];
    fen.split(" ")[0].split("/").forEach((row, i) => {
      let f = 0;
      for (const ch of row) {
        if (/\d/.test(ch)) { f += Number(ch); continue; }
        out.push({ sq: String.fromCharCode(97 + f) + (8 - i), type: ch.toLowerCase(), white: ch === ch.toUpperCase() });
        f += 1;
      }
    });
    return out;
  };

  // ---------- board ----------
  function buildBoard() {
    const s = svg("svg", { viewBox: "0 0 800 800", role: "img", "aria-label": "Chess board" });
    const defs = svg("defs");
    MARKS.forEach((i) => {
      const m = svg("marker", { id: `ah-${i}`, viewBox: "0 0 10 10", refX: "5", refY: "5", markerWidth: "3", markerHeight: "3", orient: "auto-start-reverse" });
      m.appendChild(svg("path", { d: "M0 0 L10 5 L0 10 z", class: `m${i}` }));
      defs.appendChild(m);
    });
    for (const [key, ch] of Object.entries(PIECES)) {
      for (const white of [true, false]) {
        const sym = svg("symbol", { id: `pc-${white ? "w" : "b"}${key}`, viewBox: "0 0 100 100" });
        sym.innerHTML = `<g class="${white ? "pc-w" : "pc-b"}">${ch.body}</g>` +
          (ch.detail ? `<g class="${white ? "pc-detail-w" : "pc-detail-b"}">${ch.detail}</g>` : "");
        defs.appendChild(sym);
      }
    }
    s.appendChild(defs);
    const squares = svg("g");
    for (let r = 0; r < 8; r++) for (let f = 0; f < 8; f++) {
      squares.appendChild(svg("rect", { x: f * SQ, y: r * SQ, width: SQ, height: SQ, class: (r + f) % 2 ? "sq-d" : "sq-l" }));
    }
    for (let f = 0; f < 8; f++) {
      const t = svg("text", { x: f * SQ + SQ - 8, y: 792, "text-anchor": "end", class: `coord ${f % 2 ? "coord-d" : "coord-l"}` });
      t.textContent = String.fromCharCode(97 + f);
      squares.appendChild(t);
    }
    for (let r = 0; r < 8; r++) {
      const t = svg("text", { x: 6, y: r * SQ + 18, class: `coord ${r % 2 ? "coord-l" : "coord-d"}` });
      t.textContent = String(8 - r);
      squares.appendChild(t);
    }
    s.appendChild(squares);
    for (const id of ["l-control", "l-last", "l-network", "l-pieces", "l-overload", "l-marks", "l-preview"]) s.appendChild(svg("g", { id }));
    $("#board").appendChild(s);
    wireDrawing(s);
  }

  function squareAt(svgEl, evt) {
    const r = svgEl.getBoundingClientRect();
    const f = Math.floor(((evt.clientX - r.left) / r.width) * 8);
    const rank = 7 - Math.floor(((evt.clientY - r.top) / r.height) * 8);
    if (f < 0 || f > 7 || rank < 0 || rank > 7) return null;
    return String.fromCharCode(97 + f) + (rank + 1);
  }

  function wireDrawing(svgEl) {
    const board = $("#board");
    board.addEventListener("contextmenu", (e) => e.preventDefault());
    board.addEventListener("pointerdown", (e) => {
      const right = e.button === 2;
      if (!right && !(state.drawMode && e.button === 0)) return;
      const sq = squareAt(svgEl, e);
      if (!sq) return;
      e.preventDefault();
      board.setPointerCapture(e.pointerId);
      state.drag = { from: sq, to: sq };
    });
    board.addEventListener("pointermove", (e) => {
      if (!state.drag) return;
      const sq = squareAt(svgEl, e);
      if (sq && sq !== state.drag.to) { state.drag.to = sq; renderPreview(); }
    });
    const finish = (e) => {
      if (!state.drag) return;
      const { from, to } = state.drag;
      state.drag = null;
      $("#l-preview").innerHTML = "";
      if (e.type === "pointercancel") return;
      const marks = marksHere();
      if (from === to) {
        const i = marks.circles.findIndex((c) => c.sq === from);
        if (i >= 0) marks.circles.splice(i, 1); else marks.circles.push({ sq: from, mark: state.mark });
      } else {
        const i = marks.arrows.findIndex((a) => a.from === from && a.to === to);
        if (i >= 0) marks.arrows.splice(i, 1); else marks.arrows.push({ from, to, mark: state.mark });
      }
      renderMarks();
    };
    board.addEventListener("pointerup", finish);
    board.addEventListener("pointercancel", finish);
  }

  const marksHere = () => (state.marks[state.k] ||= { arrows: [], circles: [] });

  function arrowEl(from, to, mark, opacity = 0.85) {
    const a = sqXY(from), b = sqXY(to);
    const dx = b.cx - a.cx, dy = b.cy - a.cy, len = Math.hypot(dx, dy) || 1;
    return svg("line", { x1: a.cx, y1: a.cy, x2: b.cx - (dx / len) * 30, y2: b.cy - (dy / len) * 30, class: `m${mark}`,
      "stroke-width": 14, "stroke-linecap": "round", opacity, "marker-end": `url(#ah-${mark})` });
  }

  function renderPreview() {
    const g = $("#l-preview");
    g.innerHTML = "";
    const d = state.drag;
    if (d && d.from !== d.to) g.appendChild(arrowEl(d.from, d.to, state.mark, 0.45));
  }

  function renderMarks() {
    const g = $("#l-marks");
    g.innerHTML = "";
    const m = state.marks[state.k];
    if (!m) return;
    for (const c of m.circles) {
      const p = sqXY(c.sq);
      g.appendChild(svg("circle", { cx: p.cx, cy: p.cy, r: 44, class: `mark-ring m${c.mark}` }));
    }
    for (const a of m.arrows) g.appendChild(arrowEl(a.from, a.to, a.mark));
  }

  function renderBoard(f) {
    const L = state.layers;
    const clear = (id) => { const g = $(`#${id}`); g.innerHTML = ""; return g; };
    const ctl = clear("l-control");
    if (L.control) for (const [sq, side] of Object.entries(f.structure.control)) {
      const p = sqXY(sq);
      ctl.appendChild(svg("rect", { x: p.x, y: p.y, width: SQ, height: SQ, class: side === "w" ? "ctl-w" : "ctl-b" }));
    }
    const last = clear("l-last");
    if (f.lastMove) for (const sq of [f.lastMove.uci.slice(0, 2), f.lastMove.uci.slice(2, 4)]) {
      const p = sqXY(sq);
      last.appendChild(svg("rect", { x: p.x, y: p.y, width: SQ, height: SQ, class: "lastmove" }));
    }
    const net = clear("l-network");
    if (L.network) for (const [a, b, side] of f.structure.defends) {
      const p = sqXY(a), q = sqXY(b);
      net.appendChild(svg("line", { x1: p.cx, y1: p.cy, x2: q.cx, y2: q.cy, class: side === "w" ? "net-w" : "net-b" }));
    }
    const pieces = clear("l-pieces");
    for (const pc of parseFen(f.fen)) {
      const p = sqXY(pc.sq);
      pieces.appendChild(svg("use", { href: `#pc-${pc.white ? "w" : "b"}${pc.type}`, x: p.x + 6, y: p.y + 6, width: SQ - 12, height: SQ - 12 }));
    }
    const over = clear("l-overload");
    if (L.overload) for (const o of f.structure.overloaded) {
      const p = sqXY(o.square);
      over.appendChild(svg("rect", { x: p.x + 5, y: p.y + 5, width: SQ - 10, height: SQ - 10, rx: 16, class: "ring" }));
      for (const d of o.duties) {
        const q = sqXY(d.target), hot = d.attacked ? " hot" : "";
        over.appendChild(svg("line", { x1: p.cx, y1: p.cy, x2: q.cx, y2: q.cy, class: `duty${hot}` }));
        over.appendChild(svg("circle", { cx: q.cx, cy: q.cy, r: 11, class: `duty-pt${hot}` }));
      }
    }
    renderMarks();
  }

  // ---------- summary around the board ----------
  function renderSummary(f) {
    const g = state.feed.game;
    $("#clk-w").textContent = fmtClock(f.clocks.white);
    $("#clk-b").textContent = fmtClock(f.clocks.black);
    $("#pl-w").classList.toggle("to-move", f.toMove === "white" && !!f.decision);
    $("#pl-b").classList.toggle("to-move", f.toMove === "black" && !!f.decision);
    $("#mv").textContent = f.lastMove ? f.lastMove.label : "Start";
    $("#mv-lbl").textContent = f.decision ? "Last move" : `Final · ${g.result}`;

    const dec = f.decision;
    const c = f.callouts[0];
    const flag = c ? `<span class="flag ${c.kind}" title="${esc(c.body)}">${esc(c.title)}</span>` : "";
    $("#summary").innerHTML = dec
      ? `<span class="lead">${esc(surname(dec.side === "white" ? g.white : g.black))} to move</span>
         <span class="kv find">Find rate <b>${pct(dec.findRate)}%</b></span>
         <span class="kv risk">Blunder risk <b>${pct(dec.pBlunder)}%</b></span>${flag}`
      : `<span class="lead">Game over · ${esc(g.result)}</span>${flag}`;

    for (const [name, wdl] of [["engine", f.engine.wdl], ["human", humanWdl(f)]]) {
      const [w, d, b] = wdl;
      const v = $(`.vbar.${name}`);
      v.querySelector(".seg.white").style.cssText = `top:${(1 - w) * 100}%;height:${w * 100}%`;
      v.querySelector(".seg.draw").style.cssText = `top:${b * 100}%;height:${d * 100}%`;
      v.querySelector(".val.w").textContent = w >= 0.06 ? pct(w) : "";
      v.querySelector(".val.b").textContent = b >= 0.06 ? pct(b) : "";
      v.title = `${name === "engine" ? "Engine" : "Human"}: White ${pct(w)}% · draw ${pct(d)}% · Black ${pct(b)}%`;
    }
  }

  // ---------- colour scheme ----------
  function setScheme(name) {
    if (!SCHEMES.includes(name)) name = SCHEMES[0];
    if (name === SCHEMES[0]) delete document.documentElement.dataset.scheme;
    else document.documentElement.dataset.scheme = name;
    $("#scheme").value = name;
    try { localStorage.setItem("chessviewer.scheme", name); } catch (e) { /* storage unavailable */ }
  }

  // ---------- tabs ----------
  function setTab(tab) {
    state.tab = tab;
    document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === tab)));
    document.querySelectorAll(".pane").forEach((p) => { p.hidden = p.id !== `pane-${tab}`; });
    try { localStorage.setItem("chessviewer.tab", tab); } catch (e) { /* storage unavailable */ }
  }

  function youtubeEmbed(url) {
    const m = String(url).match(/(?:youtube\.com\/(?:watch\?v=|live\/|embed\/)|youtu\.be\/)([\w-]{11})/);
    return m ? `https://www.youtube.com/embed/${m[1]}` : null;
  }

  function renderVideo() {
    const slot = $("#video-slot");
    const url = state.videoUrl;
    if (url) {
      const embed = youtubeEmbed(url);
      $("#video-src").textContent = embed ? "YouTube" : "External video";
      slot.innerHTML = (embed ? `<div class="video"><iframe src="${esc(embed)}" title="Match broadcast" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe></div>` : "") +
        `<p class="foot">If the video does not appear, this page cannot embed it here: <a href="${esc(url)}" target="_blank" rel="noopener">open the broadcast in a new tab</a>.</p>`;
      return;
    }
    $("#video-src").textContent = "Not linked";
    slot.innerHTML = `<div class="empty">
        <p>No broadcast video is linked to this game. Paste a YouTube link to watch alongside the board.</p>
        <form id="video-form"><input id="video-url" type="url" placeholder="https://www.youtube.com/watch?v=…" aria-label="Video link" required><button class="btn primary" type="submit">Add video</button></form>
      </div>`;
    $("#video-form").addEventListener("submit", (e) => {
      e.preventDefault();
      state.videoUrl = $("#video-url").value.trim();
      renderVideo();
    });
  }

  // Game tab
  const CH = { w: 640, h: 230, l: 50, r: 12, t: 14, b: 26 };
  const chartXY = (k, e) => {
    const n = state.feed.frames.length - 1;
    return [CH.l + (k / n) * (CH.w - CH.l - CH.r), CH.t + (1 - e) * (CH.h - CH.t - CH.b)];
  };

  function buildChart() {
    const frames = state.feed.frames, n = frames.length - 1;
    const s = svg("svg", { viewBox: `0 0 ${CH.w} ${CH.h}`, role: "img", "aria-label": "White's expected points over the game" });
    for (const [e, label] of [[1, "White"], [0.75, "75%"], [0.5, "50%"], [0.25, "25%"], [0, "Black"]]) {
      const [, y] = chartXY(0, e);
      s.appendChild(svg("line", { x1: CH.l, x2: CH.w - CH.r, y1: y, y2: y, class: e === 0.5 ? "mid" : "grid" }));
      const t = svg("text", { x: CH.l - 8, y: y + 4, "text-anchor": "end" });
      t.textContent = label;
      s.appendChild(t);
    }
    for (let mv = 10; mv * 2 <= n; mv += 10) {
      const [x] = chartXY(mv * 2, 0);
      const t = svg("text", { x, y: CH.h - 6, "text-anchor": "middle" });
      t.textContent = `move ${mv}`;
      s.appendChild(t);
    }
    const eng = frames.map((f, k) => chartXY(k, expWhite(f.engine.wdl)));
    const hum = frames.filter((f) => f.human).map((f) => chartXY(f.ply, expWhite(humanWdl(f))));
    const d = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
    const [, y0] = chartXY(0, 0);
    s.appendChild(svg("path", { d: `${d(eng)} L${eng[eng.length - 1][0]} ${y0} L${eng[0][0]} ${y0} Z`, class: "area" }));
    s.appendChild(svg("path", { d: d(hum), class: "hum" }));
    s.appendChild(svg("path", { d: d(eng), class: "eng" }));
    s.appendChild(svg("line", { id: "chart-now", y1: CH.t, y2: CH.h - CH.b, class: "now" }));
    const key = svg("g");
    key.innerHTML = `<rect x="${CH.l + 10}" y="${CH.t + 6}" width="14" height="4" rx="1" class="key-eng"/><text x="${CH.l + 30}" y="${CH.t + 12}">Engine</text>
      <rect x="${CH.l + 92}" y="${CH.t + 6}" width="14" height="4" rx="1" class="key-hum"/><text x="${CH.l + 112}" y="${CH.t + 12}">Human (Maia-3)</text>`;
    s.appendChild(key);
    s.style.cursor = "pointer";
    s.addEventListener("click", (e) => {
      const r = s.getBoundingClientRect();
      const x = ((e.clientX - r.left) / r.width) * CH.w;
      stop();
      showFrame(Math.round(((x - CH.l) / (CH.w - CH.l - CH.r)) * n));
    });
    $("#chart").appendChild(s);
  }

  function buildMoments() {
    const frames = state.feed.frames;
    const items = [];
    for (const f of frames) for (const c of f.callouts) items.push({ ply: f.ply, kind: c.kind, title: c.title, body: c.body });
    for (const s of state.feed.swings) {
      if (!items.some((i) => i.ply === s.ply && i.kind === "swing")) {
        items.push({ ply: s.ply, kind: "swing", title: "Swing moment", body: `${s.label} cost ${s.mover === "white" ? "White" : "Black"} ${Math.abs(s.epa).toFixed(2)} expected points` });
      }
    }
    items.sort((a, b) => a.ply - b.ply);
    $("#moments").innerHTML = items.map((i) => `
      <button class="moment" type="button" data-ply="${i.ply}">
        <span class="mv">${esc(frames[i.ply].lastMove ? frames[i.ply].lastMove.label : "Start")}</span>
        <span class="txt"><span class="flag ${i.kind}">${KIND_LABEL[i.kind]}</span>${esc(i.body)}</span>
      </button>`).join("");
    $("#moments").addEventListener("click", (e) => {
      const b = e.target.closest(".moment");
      if (b) { stop(); showFrame(Number(b.dataset.ply)); }
    });
  }

  function buildMoves() {
    const frames = state.feed.frames;
    let html = "";
    for (let k = 1; k < frames.length; k += 2) {
      html += `<span class="no">${Math.ceil(k / 2)}.</span>${mvBtn(frames[k])}${frames[k + 1] ? mvBtn(frames[k + 1]) : "<span></span>"}`;
    }
    $("#movelist").innerHTML = html;
    $("#movelist").addEventListener("click", (e) => {
      const b = e.target.closest(".mvbtn");
      if (b) { stop(); showFrame(Number(b.dataset.ply)); }
    });
  }
  function mvBtn(f) {
    const e = f.epa ? f.epa.value : 0;
    const chip = Math.abs(e) >= 0.05 ? `<span class="e neg">${e.toFixed(2)}</span>` : "";
    return `<button class="mvbtn" type="button" data-ply="${f.ply}"><span>${esc(f.lastMove.san)}</span>${chip}</button>`;
  }

  function renderPanel(f) {
    const g = state.feed.game;
    for (const [name, wdl] of [["engine", f.engine.wdl], ["human", humanWdl(f)]]) {
      const spans = $(`#hb-${name}`).querySelectorAll("span");
      wdl.forEach((x, i) => { spans[i].style.flexBasis = `${x * 100}%`; spans[i].textContent = x >= 0.1 ? `${pct(x)}%` : ""; });
    }
    const [x] = chartXY(f.ply, 0);
    $("#chart-now").setAttribute("x1", x);
    $("#chart-now").setAttribute("x2", x);
    document.querySelectorAll(".moment").forEach((m) => m.classList.toggle("current", Number(m.dataset.ply) === f.ply));
    document.querySelectorAll(".mvbtn").forEach((m) => m.classList.toggle("current", Number(m.dataset.ply) === f.ply));

    const dec = f.decision;
    if (dec) {
      $("#dec-title").textContent = `${surname(dec.side === "white" ? g.white : g.black)} to move`;
      $("#dec-find").innerHTML = `${pct(dec.findRate)}<small>%</small>`;
      $("#dec-risk").innerHTML = `${pct(dec.pBlunder)}<small>%</small>`;
      $("#dec-find-m").style.width = `${dec.findRate * 100}%`;
      $("#dec-risk-m").style.width = `${dec.pBlunder * 100}%`;
      $("#cands").innerHTML = dec.humanTop.map((c) => `
        <div class="cand"><b>${esc(c.san)}</b><div class="pb"><i style="width:${Math.max(2, c.p * 100)}%"></i></div><span class="p">${pct(c.p)}%</span>
        <span class="chip ${c.holds ? "ok" : "bad"}">${c.holds ? "Holds" : "Loses"}</span></div>`).join("");
      const ev = f.engine.mate != null ? `mate in ${Math.abs(f.engine.mate)}` : `${f.engine.cp > 0 ? "+" : ""}${(f.engine.cp / 100).toFixed(2)}`;
      $("#engline").innerHTML = `<span>Engine's best move<b>${esc(f.engine.best.san)}</b></span><span>Evaluation<b>${ev}</b></span><span>Moves that hold<b>${dec.goodMoves}</b></span>`;
      $("#engline").hidden = false;
    } else {
      $("#dec-title").textContent = `Game over · ${g.result}`;
      $("#dec-find").textContent = "–";
      $("#dec-risk").textContent = "–";
      $("#dec-find-m").style.width = "0";
      $("#dec-risk-m").style.width = "0";
      $("#cands").innerHTML = "";
      $("#engline").hidden = true;
    }
    $("#dec-last").innerHTML = f.epa ? `${esc(f.lastMove.label)} · ${f.epa.value >= 0 ? "+" : ""}${f.epa.value.toFixed(2)} pts` : "";
    const src = state.feed.sources;
    $("#dec-foot").textContent = `A move holds if it stays within ${src.tau} expected points of the engine's best (${src.engine}, depth ${src.depth.split("-")[0].slice(1)}). Human moves and outcomes from ${src.humanModel}, conditioned on both ratings; its outcome view is averaged over two plies.`;

    const st = f.structure;
    const whites = new Set(parseFen(f.fen).filter((p) => p.white).map((p) => p.sq));
    const duel = (label, w, b, max) => `
      <div class="duelrow"><span class="n">${w}</span><div class="half left"><i style="width:${Math.min(100, (w / max) * 100)}%"></i></div>
      <span class="l">${label}</span><div class="half right"><i style="width:${Math.min(100, (b / max) * 100)}%"></i></div><span class="n r">${b}</span></div>`;
    const kd = (v) => Math.max(0, Math.round(v));
    $("#duel").innerHTML =
      duel("Space", st.space.white, st.space.black, 16) +
      duel("King danger", kd(st.kingDanger.white), kd(st.kingDanger.black), 20) +
      duel("Loose pieces", st.loose.filter((s) => whites.has(s)).length, st.loose.filter((s) => !whites.has(s)).length, 6);
    $("#loads").innerHTML = st.overloaded.length
      ? st.overloaded.slice(0, 4).map((o) => `<div class="card load">${esc(o.caption)}</div>`).join("")
      : '<div class="card load none">No overloaded pieces in this position.</div>';
  }

  // ---------- navigation ----------
  function showFrame(k) {
    const frames = state.feed.frames;
    k = Math.max(0, Math.min(frames.length - 1, k));
    state.k = k;
    const f = frames[k];
    renderBoard(f);
    renderSummary(f);
    renderPanel(f);
    $("#timeline").value = String(k);
  }

  function stop() { if (state.playing) play(false); }
  function play(on) {
    state.playing = on;
    $("#play").textContent = on ? "Pause" : "Play";
    clearInterval(state.timer);
    if (on) {
      if (state.k >= state.feed.frames.length - 1) showFrame(0);
      state.timer = setInterval(() => {
        if (state.k >= state.feed.frames.length - 1) return play(false);
        showFrame(state.k + 1);
      }, 1800);
    }
  }

  function wireControls() {
    $("#play").addEventListener("click", () => play(!state.playing));
    $("#prev").addEventListener("click", () => { stop(); showFrame(state.k - 1); });
    $("#next").addEventListener("click", () => { stop(); showFrame(state.k + 1); });
    $("#first").addEventListener("click", () => { stop(); showFrame(0); });
    $("#last").addEventListener("click", () => { stop(); showFrame(state.feed.frames.length - 1); });
    const tl = $("#timeline");
    tl.max = String(state.feed.frames.length - 1);
    tl.addEventListener("input", () => { stop(); showFrame(Number(tl.value)); });
    $("#draw").addEventListener("click", () => {
      state.drawMode = !state.drawMode;
      $("#draw").setAttribute("aria-pressed", String(state.drawMode));
      $("#board").classList.toggle("drawing", state.drawMode);
    });
    document.querySelectorAll(".swatch").forEach((b) => b.addEventListener("click", () => {
      state.mark = Number(b.dataset.mark);
      document.querySelectorAll(".swatch").forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
    }));
    $("#clear").addEventListener("click", () => { delete state.marks[state.k]; renderMarks(); });
    document.querySelectorAll("[data-layer]").forEach((box) => box.addEventListener("change", () => {
      state.layers[box.dataset.layer] = box.checked;
      renderBoard(state.feed.frames[state.k]);
    }));
    document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.dataset.tab)));
    $("#scheme").addEventListener("change", (e) => setScheme(e.target.value));
    document.addEventListener("keydown", (e) => {
      if (e.target.closest("input, select, textarea")) return;
      if (e.key === "ArrowRight") { stop(); showFrame(state.k + 1); }
      else if (e.key === "ArrowLeft") { stop(); showFrame(state.k - 1); }
      else if (e.key === "Home") { stop(); showFrame(0); }
      else if (e.key === "End") { stop(); showFrame(state.feed.frames.length - 1); }
    });
  }

  async function loadFeed() {
    if (window.FEED) return window.FEED;
    const res = await fetch("feed.json");
    if (!res.ok) throw new Error(`feed.json returned HTTP ${res.status}`);
    return res.json();
  }

  async function start() {
    try {
      state.feed = await loadFeed();
    } catch (err) {
      document.querySelector(".app").innerHTML = `<p>Could not load the game feed (${esc(err.message)}). Generate it with <code>chessmetrics feed</code>.</p>`;
      return;
    }
    const g = state.feed.game;
    $("#name-w").textContent = surname(g.white);
    $("#name-b").textContent = surname(g.black);
    $("#meta-w").textContent = [g.whiteElo, g.whiteTeam].filter(Boolean).join(" · ");
    $("#meta-b").textContent = [g.blackElo, g.blackTeam].filter(Boolean).join(" · ");
    state.videoUrl = state.feed.video ? state.feed.video.url : null;
    buildBoard();
    buildChart();
    buildMoments();
    buildMoves();
    renderVideo();
    wireControls();
    let scheme = location.hash.slice(1);
    if (!SCHEMES.includes(scheme)) {
      try { scheme = localStorage.getItem("chessviewer.scheme") || SCHEMES[0]; } catch (e) { scheme = SCHEMES[0]; }
    }
    setScheme(scheme);
    let tab = state.videoUrl ? "video" : "game";
    try { tab = localStorage.getItem("chessviewer.tab") || tab; } catch (e) { /* storage unavailable */ }
    setTab(tab);
    // open on the first difficulty moment so the first view shows the metrics working
    const opener = state.feed.frames.find((f) => f.callouts.some((c) => c.kind === "difficulty"));
    showFrame(opener ? opener.ply : 0);
  }

  window.ChessViewer = { showFrame: (k) => showFrame(k) };
  start();
})();
