/* Chess broadcast overlay prototype.
 *
 * Reads a feed produced by `chessmetrics feed` (one frame per position) from
 * window.FEED (feed.js) or feed.json, and renders a 1920x1080 broadcast stage:
 * score bug, engine/human outcome bars, an SVG board with metric layers, an
 * analysis panel and event callouts. A live service would push the same frames
 * over a WebSocket; `showFrame` is the only entry point it needs.
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

  // ---------- state ----------
  const state = {
    feed: null, k: 0, playing: false, timer: null, speedMs: 2600, autoTab: true, tab: "game",
    layers: { lastMove: true, best: true, human: true, overload: true, control: false, network: false },
    calloutTimer: null,
  };

  // ---------- helpers ----------
  const $ = (sel, root = document) => root.querySelector(sel);
  const el = (tag, attrs = {}, html = "") => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (html) n.innerHTML = html;
    return n;
  };
  const svg = (tag, attrs = {}) => {
    const n = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  };
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

  // ---------- build the stage ----------
  function build() {
    const g = state.feed.game;
    const stage = $("#stage");
    stage.innerHTML = `
      <div class="bug">
        <div class="live"><i></i>Live</div>
        <div class="player" id="bug-w"><span class="swatch w"></span><span class="name">${surname(g.white)}</span><span class="elo">${g.whiteElo}</span><span class="clock" id="clk-w"></span></div>
        <div class="player" id="bug-b"><span class="swatch b"></span><span class="name">${surname(g.black)}</span><span class="elo">${g.blackElo}</span><span class="clock" id="clk-b"></span></div>
      </div>
      <div class="evalcol">
        <div class="vbar engine"><div class="track"><div class="seg draw"></div><div class="seg white"></div><div class="val w"></div><div class="val d"></div><div class="val b"></div></div><div class="tag">ENGINE</div></div>
        <div class="vbar human"><div class="track"><div class="seg draw"></div><div class="seg white"></div><div class="val w"></div><div class="val d"></div><div class="val b"></div></div><div class="tag">HUMAN</div></div>
      </div>
      <div class="board" id="board"></div>
      <div class="panel">
        <div class="event"><span>${g.event.split("|")[0].trim()}</span><span>Round ${String(g.round).split(".")[0]} · Classical</span></div>
        <div class="score">
          <div class="side"><span class="team">${g.whiteTeam || "White"}</span><span class="pname">${surname(g.white)}</span></div>
          <div class="mid"><span class="num" id="sc-w">–</span><span class="bar"></span><div class="mv"><span class="move" id="sc-move">Start</span><span class="cap">Engine expected score %</span></div><span class="bar"></span><span class="num" id="sc-b">–</span></div>
          <div class="side r"><span class="team">${g.blackTeam || "Black"}</span><span class="pname">${surname(g.black)}</span></div>
        </div>
        <div class="tabs" role="tablist">
          <button class="tab" role="tab" id="tab-game" data-tab="game">Game</button>
          <button class="tab" role="tab" id="tab-decision" data-tab="decision">Decision</button>
          <button class="tab" role="tab" id="tab-board" data-tab="board">Board</button>
        </div>
        <section class="pane" id="pane-game">
          <div class="h"><h3>Win probability</h3><small>White · Draw · Black</small></div>
          <div class="wp">
            <div class="wprow engine"><span class="lbl">ENGINE</span><div class="hbar" id="hb-engine"><span class="w"></span><span class="d"></span><span class="b"></span></div></div>
            <div class="wprow human"><span class="lbl">HUMAN</span><div class="hbar" id="hb-human"><span class="w"></span><span class="d"></span><span class="b"></span></div></div>
          </div>
          <div class="h"><h3>Expected points</h3><small>White's share, move by move</small></div>
          <div class="chart" id="chart"></div>
          <div class="h"><h3>Swing moments</h3><small>Largest expected-points changes</small></div>
          <div class="swings" id="swings"></div>
        </section>
        <section class="pane" id="pane-decision">
          <div class="h"><h3 id="dec-title">Next decision</h3><small id="dec-last"></small></div>
          <div class="decide">
            <div class="stat"><span class="k">Find rate</span><span class="v" id="dec-find"></span><div class="meter find"><i id="dec-find-m"></i></div><span class="sub">chance a player at this level finds a move that holds</span></div>
            <div class="stat"><span class="k">Blunder risk</span><span class="v" id="dec-risk"></span><div class="meter risk"><i id="dec-risk-m"></i></div><span class="sub">chance the next move loses more than 0.15 points</span></div>
          </div>
          <div class="h"><h3>Most likely human moves</h3><small>Maia-3 at these ratings</small></div>
          <div class="cands" id="cands"></div>
          <div class="engline" id="engline"></div>
          <p class="foot" id="dec-foot"></p>
        </section>
        <section class="pane" id="pane-board">
          <div class="h"><h3>Position</h3><small>Structural metrics, no engine</small></div>
          <div class="duel" id="duel"></div>
          <div class="h"><h3>Overloaded pieces</h3><small>Sole defender of two or more duties</small></div>
          <div class="loads" id="loads"></div>
          <div class="h"><h3>Board layers</h3><small>Toggle in the operator panel</small></div>
          <div class="legend" id="legend"></div>
        </section>
      </div>
      <div class="callout" id="callout"></div>`;
    stage.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => { state.autoTab = false; $("#auto-tab").checked = false; setTab(t.dataset.tab); }));
    buildBoard();
    buildChart();
    buildScrubber();
  }

  function buildBoard() {
    const s = svg("svg", { viewBox: "0 0 800 800", role: "img", "aria-label": "Chess board" });
    const defs = svg("defs");
    for (const [id, color] of [["ah-best", "var(--lime)"], ["ah-human", "var(--orchid)"]]) {
      const m = svg("marker", { id, viewBox: "0 0 10 10", refX: "5", refY: "5", markerWidth: "3.2", markerHeight: "3.2", orient: "auto-start-reverse" });
      const p = svg("path", { d: "M0 0 L10 5 L0 10 z" });
      p.style.fill = color;
      m.appendChild(p);
      defs.appendChild(m);
    }
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
      const t = svg("text", { x: f * SQ + SQ - 8, y: 792, "text-anchor": "end", class: "coord" });
      t.textContent = String.fromCharCode(97 + f);
      t.style.fill = f % 2 ? "var(--sq-dark)" : "var(--sq-light)";
      squares.appendChild(t);
    }
    for (let r = 0; r < 8; r++) {
      const t = svg("text", { x: 6, y: r * SQ + 18, class: "coord" });
      t.textContent = String(8 - r);
      t.style.fill = r % 2 ? "var(--sq-light)" : "var(--sq-dark)";
      squares.appendChild(t);
    }
    s.appendChild(squares);
    for (const id of ["l-control", "l-last", "l-network", "l-overload-under", "l-pieces", "l-overload", "l-arrows"]) s.appendChild(svg("g", { id }));
    $("#board").appendChild(s);
  }

  // ---------- chart ----------
  const CH = { w: 686, h: 270, l: 54, r: 14, t: 16, b: 30 };
  function chartXY(k, e) {
    const n = state.feed.frames.length - 1;
    return [CH.l + (k / n) * (CH.w - CH.l - CH.r), CH.t + (1 - e) * (CH.h - CH.t - CH.b)];
  }
  function buildChart() {
    const frames = state.feed.frames;
    const s = svg("svg", { viewBox: `0 0 ${CH.w} ${CH.h}`, role: "img", "aria-label": "White's expected points over the game" });
    s.innerHTML = `<defs>
        <linearGradient id="humgrad" x1="0" x2="1"><stop offset="0" stop-color="#ff8a3d"/><stop offset="1" stop-color="#c24dff"/></linearGradient>
        <linearGradient id="areagrad" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#d9f84a" stop-opacity=".22"/><stop offset="1" stop-color="#d9f84a" stop-opacity="0"/></linearGradient>
      </defs>`;
    for (const [e, label] of [[1, "White"], [0.75, "75%"], [0.5, "50%"], [0.25, "25%"], [0, "Black"]]) {
      const [, y] = chartXY(0, e);
      s.appendChild(svg("line", { x1: CH.l, x2: CH.w - CH.r, y1: y, y2: y, class: e === 0.5 ? "mid" : "grid" }));
      const t = svg("text", { x: CH.l - 8, y: y + 4, "text-anchor": "end" });
      t.textContent = label;
      s.appendChild(t);
    }
    const n = frames.length - 1;
    for (let mv = 10; mv * 2 <= n; mv += 10) {
      const [x] = chartXY(mv * 2, 0);
      const t = svg("text", { x, y: CH.h - 8, "text-anchor": "middle" });
      t.textContent = `move ${mv}`;
      s.appendChild(t);
    }
    const eng = frames.map((f, k) => chartXY(k, expWhite(f.engine.wdl)));
    const hum = frames.filter((f) => f.human).map((f) => chartXY(f.ply, expWhite(f.human.wdlSmooth)));
    const d = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(" ");
    const [, y0] = chartXY(0, 0);
    s.appendChild(svg("path", { d: `${d(eng)} L${eng[eng.length - 1][0]} ${y0} L${eng[0][0]} ${y0} Z`, class: "area" }));
    s.appendChild(svg("path", { d: d(hum), class: "hum" }));
    s.appendChild(svg("path", { d: d(eng), class: "eng" }));
    for (const sw of state.feed.swings) {
      const [x, y] = eng[sw.ply];
      s.appendChild(svg("circle", { cx: x, cy: y, r: 7, class: "swing" }));
    }
    s.appendChild(svg("line", { id: "chart-now", y1: CH.t, y2: CH.h - CH.b, class: "now" }));
    const key = svg("g");
    key.innerHTML = `<rect x="${CH.l + 10}" y="${CH.t + 6}" width="14" height="4" rx="2" fill="#d9f84a"/><text x="${CH.l + 30}" y="${CH.t + 12}">Engine</text>
      <rect x="${CH.l + 96}" y="${CH.t + 6}" width="14" height="4" rx="2" fill="#c24dff"/><text x="${CH.l + 116}" y="${CH.t + 12}">Human (Maia-3)</text>`;
    s.appendChild(key);
    $("#chart").appendChild(s);
  }

  // ---------- operator controls ----------
  function buildScrubber() {
    const scrub = $("#scrub");
    scrub.innerHTML = '<div class="fill"></div>';
    const n = state.feed.frames.length - 1;
    state.feed.frames.forEach((f) => {
      for (const c of f.callouts) {
        const t = el("div", { class: `tick ${c.kind}`, title: `${f.lastMove ? f.lastMove.label : "Start"}: ${c.title}` });
        t.style.left = `${(f.ply / n) * 100}%`;
        scrub.appendChild(t);
      }
    });
    scrub.appendChild(el("div", { class: "head" }));
    scrub.addEventListener("click", (e) => {
      const r = scrub.getBoundingClientRect();
      showFrame(Math.round(((e.clientX - r.left) / r.width) * n));
    });
  }

  // ---------- rendering ----------
  function setTab(tab) {
    state.tab = tab;
    document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === tab)));
    document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("on", p.id === `pane-${tab}`));
  }

  function renderBars(f) {
    const views = [["engine", f.engine.wdl], ["human", f.human ? f.human.wdlSmooth : f.engine.wdl]];
    for (const [name, wdl] of views) {
      const [w, d, b] = wdl;
      const v = $(`.vbar.${name}`);
      const H = v.querySelector(".track").clientHeight || 760;
      v.querySelector(".seg.white").style.cssText = `top:${(1 - w) * 100}%;height:${w * 100}%`;
      v.querySelector(".seg.draw").style.cssText = `top:${b * 100}%;height:${d * 100}%`;
      const vw = v.querySelector(".val.w"), vb = v.querySelector(".val.b");
      vw.textContent = w >= 0.06 ? pct(w) : "";
      vw.style.top = `${Math.min(H - 40, (1 - w) * H + 8)}px`;
      vb.textContent = b >= 0.06 ? pct(b) : "";
      vb.style.top = "10px";
      const vd = v.querySelector(".val.d");
      vd.textContent = d >= 0.12 ? `${pct(d)}` : "";
      vd.style.top = `${(b + d / 2) * H - 16}px`;
      const hb = $(`#hb-${name}`);
      const spans = hb.querySelectorAll("span");
      [w, d, b].forEach((x, i) => {
        spans[i].style.flexBasis = `${x * 100}%`;
        spans[i].textContent = x >= 0.09 ? `${pct(x)}%` : "";
      });
    }
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
    if (L.lastMove && f.lastMove) for (const sq of [f.lastMove.uci.slice(0, 2), f.lastMove.uci.slice(2, 4)]) {
      const p = sqXY(sq);
      last.appendChild(svg("rect", { x: p.x, y: p.y, width: SQ, height: SQ, class: "lastmove" }));
    }
    const net = clear("l-network");
    if (L.network) for (const [a, b, side] of f.structure.defends) {
      const p = sqXY(a), q = sqXY(b);
      net.appendChild(svg("line", { x1: p.cx, y1: p.cy, x2: q.cx, y2: q.cy, class: side === "w" ? "net-w" : "net-b" }));
    }
    const under = clear("l-overload-under"), over = clear("l-overload");
    if (L.overload) for (const o of f.structure.overloaded) {
      const p = sqXY(o.square);
      under.appendChild(svg("ellipse", { cx: p.cx, cy: p.y + 84, rx: 40, ry: 13, class: "ring-glow" }));
      over.appendChild(svg("rect", { x: p.x + 5, y: p.y + 5, width: SQ - 10, height: SQ - 10, rx: 16, class: "ring" }));
      for (const d of o.duties) {
        const q = sqXY(d.target);
        const hot = d.attacked ? " hot" : "";
        over.appendChild(svg("line", { x1: p.cx, y1: p.cy, x2: q.cx, y2: q.cy, class: `duty${hot}` }));
        over.appendChild(svg("circle", { cx: q.cx, cy: q.cy, r: 11, class: `duty-pt${hot}` }));
      }
    }
    const pieces = clear("l-pieces");
    for (const pc of parseFen(f.fen)) {
      const p = sqXY(pc.sq);
      pieces.appendChild(svg("use", { href: `#pc-${pc.white ? "w" : "b"}${pc.type}`, x: p.x + 6, y: p.y + 6, width: SQ - 12, height: SQ - 12 }));
    }
    const arrows = clear("l-arrows");
    const arrow = (uci, cls, marker, offset) => {
      const a = sqXY(uci.slice(0, 2)), b = sqXY(uci.slice(2, 4));
      const dx = b.cx - a.cx, dy = b.cy - a.cy, len = Math.hypot(dx, dy) || 1;
      const ox = (-dy / len) * offset, oy = (dx / len) * offset;
      const line = svg("line", { x1: a.cx + ox, y1: a.cy + oy, x2: b.cx - (dx / len) * 34 + ox, y2: b.cy - (dy / len) * 34 + oy,
        "stroke-width": 15, "stroke-linecap": "round", "marker-end": `url(#${marker})`, opacity: ".9" });
      line.style.stroke = cls;
      arrows.appendChild(line);
    };
    if (f.decision) {
      const humanTop = f.decision.humanTop[0];
      const best = f.engine.best && f.engine.best.uci;
      const same = humanTop && best === humanTop.uci;
      if (L.human && humanTop) arrow(humanTop.uci, "var(--orchid)", "ah-human", same && L.best ? -9 : 0);
      if (L.best && best) arrow(best, "var(--lime)", "ah-best", same && L.human ? 9 : 0);
    }
  }

  function renderPanel(f) {
    const g = state.feed.game, frames = state.feed.frames;
    const ew = expWhite(f.engine.wdl);
    $("#sc-w").textContent = pct(ew);
    $("#sc-b").textContent = pct(1 - ew);
    $("#sc-move").textContent = f.lastMove ? f.lastMove.label : "Start";

    // chart marker
    const [x] = chartXY(f.ply, 0);
    const now = $("#chart-now");
    now.setAttribute("x1", x);
    now.setAttribute("x2", x);

    // swings
    $("#swings").innerHTML = state.feed.swings.map((s) => `
      <div class="swingrow ${s.ply <= f.ply ? "past" : ""}"><div><b>${s.label}</b><span>${s.mover === "white" ? surname(g.white) : surname(g.black)}</span></div>
      <span class="chip neg">${s.epa.toFixed(2)} pts</span></div>`).join("");

    // decision
    const dec = f.decision;
    if (dec) {
      const who = dec.side === "white" ? surname(g.white) : surname(g.black);
      $("#dec-title").textContent = `${who} to move`;
      $("#dec-find").innerHTML = `${pct(dec.findRate)}<small>%</small>`;
      $("#dec-risk").innerHTML = `${pct(dec.pBlunder)}<small>%</small>`;
      $("#dec-find-m").style.width = `${dec.findRate * 100}%`;
      $("#dec-risk-m").style.width = `${dec.pBlunder * 100}%`;
      $("#cands").innerHTML = dec.humanTop.map((c) => `
        <div class="cand"><b>${c.san}</b><div class="pb"><i style="width:${Math.max(2, c.p * 100)}%"></i></div><span class="p">${pct(c.p)}%</span>
        <span class="chip ${c.holds ? "ok" : "bad"}">${c.holds ? "Holds" : "Loses"}</span></div>`).join("");
      const cp = f.engine.mate != null ? `mate in ${Math.abs(f.engine.mate)}` : `${f.engine.cp > 0 ? "+" : ""}${(f.engine.cp / 100).toFixed(2)}`;
      $("#engline").innerHTML = `<span>Engine's best move<b>${f.engine.best.san}</b></span><span>${cp} for White · ${dec.goodMoves} move${dec.goodMoves === 1 ? "" : "s"} hold${dec.goodMoves === 1 ? "s" : ""}</span>`;
    } else {
      $("#dec-title").textContent = `Game over · ${g.result}`;
      for (const id of ["#dec-find", "#dec-risk"]) $(id).textContent = "–";
      $("#cands").innerHTML = "";
      $("#engline").innerHTML = "";
    }
    $("#dec-last").innerHTML = f.epa ? `Last move ${f.lastMove.label} · <span class="chip ${f.epa.value < -0.04 ? "neg" : "pos"}">${f.epa.value >= 0 ? "+" : ""}${f.epa.value.toFixed(2)} pts</span>` : "";
    const src = state.feed.sources;
    $("#dec-foot").textContent = `A move "holds" if it stays within ${src.tau} expected points of the engine's best (${src.engine}, ${src.depth.split("-")[0].replace("d", "depth ")}). Human probabilities: ${src.humanModel}, conditioned on both ratings; its outcome view is averaged over two plies.`;

    // board pane
    const st = f.structure;
    const duel = (label, w, b, max) => `
      <div class="duelrow"><span class="n">${w}</span><div class="half left"><i style="width:${Math.min(100, (w / max) * 100)}%"></i></div>
      <span class="l">${label}</span><div class="half right"><i style="width:${Math.min(100, (b / max) * 100)}%"></i></div><span class="n r">${b}</span></div>`;
    const kd = (v) => Math.max(0, Math.round(v));
    $("#duel").innerHTML =
      duel("Space", st.space.white, st.space.black, 16) +
      duel("King danger", kd(st.kingDanger.white), kd(st.kingDanger.black), 20) +
      duel("Loose pieces", st.loose.filter((s) => isWhiteAt(f, s)).length, st.loose.filter((s) => !isWhiteAt(f, s)).length, 6);
    $("#loads").innerHTML = st.overloaded.length
      ? st.overloaded.slice(0, 3).map((o) => `<div class="load">${o.caption}</div>`).join("")
      : '<div class="load none">No overloaded pieces in this position.</div>';
    $("#legend").innerHTML = [["lastMove", "Last move"], ["best", "Engine best"], ["human", "Human favourite"], ["overload", "Overloaded"], ["control", "Square control"], ["network", "Defence network"]]
      .map(([k, label]) => `<span class="chip ${state.layers[k] ? "" : "off"}">${label}</span>`).join("");

    // bug
    $("#clk-w").textContent = fmtClock(f.clocks.white);
    $("#clk-b").textContent = fmtClock(f.clocks.black);
    $("#bug-w").classList.toggle("to-move", f.toMove === "white" && !!f.decision);
    $("#bug-b").classList.toggle("to-move", f.toMove === "black" && !!f.decision);
  }

  function isWhiteAt(f, sq) {
    return parseFen(f.fen).some((p) => p.sq === sq && p.white);
  }

  function renderCallout(f, animate) {
    const box = $("#callout");
    clearTimeout(state.calloutTimer);
    const c = f.callouts[0];
    if (!c || !animate) {
      if (box.classList.contains("show")) { box.classList.remove("show"); box.classList.add("hide"); }
      return;
    }
    box.innerHTML = `<div class="card k-${c.kind}"><span class="kind">${c.kind === "difficulty" ? "Difficulty" : c.kind === "swing" ? "Swing" : c.kind === "overload" ? "Overload" : "Conversion"}</span>
      <div class="title">${c.title}</div><div class="body">${c.body}</div></div>`;
    box.classList.remove("show", "hide");
    void box.offsetWidth; // restart the animation
    box.classList.add("show");
    state.calloutTimer = setTimeout(() => { box.classList.remove("show"); box.classList.add("hide"); }, 6500);
    if (state.autoTab) setTab(c.kind === "difficulty" ? "decision" : c.kind === "overload" ? "board" : "game");
  }

  function showFrame(k, animateCallout = true) {
    const frames = state.feed.frames;
    k = Math.max(0, Math.min(frames.length - 1, k));
    const f = frames[k];
    state.k = k;
    renderBars(f);
    renderBoard(f);
    renderPanel(f);
    renderCallout(f, animateCallout);
    const n = frames.length - 1;
    $("#scrub .fill").style.width = `${(k / n) * 100}%`;
    $("#scrub .head").style.left = `${(k / n) * 100}%`;
    $("#moveinfo").textContent = f.lastMove ? f.lastMove.label : "Start position";
  }

  // ---------- playback ----------
  function play(on) {
    state.playing = on;
    $("#play").textContent = on ? "Pause" : "Play";
    clearInterval(state.timer);
    if (on) state.timer = setInterval(() => {
      if (state.k >= state.feed.frames.length - 1) return play(false);
      showFrame(state.k + 1);
    }, state.speedMs);
  }

  function wireControls() {
    $("#play").addEventListener("click", () => play(!state.playing));
    $("#prev").addEventListener("click", () => { play(false); showFrame(state.k - 1); });
    $("#next").addEventListener("click", () => { play(false); showFrame(state.k + 1); });
    $("#first").addEventListener("click", () => { play(false); showFrame(0); });
    $("#last").addEventListener("click", () => { play(false); showFrame(state.feed.frames.length - 1); });
    $("#next-callout").addEventListener("click", () => {
      play(false);
      const nxt = state.feed.frames.find((f) => f.ply > state.k && f.callouts.length);
      if (nxt) showFrame(nxt.ply);
    });
    $("#speed").addEventListener("change", (e) => { state.speedMs = Number(e.target.value); if (state.playing) play(true); });
    $("#auto-tab").addEventListener("change", (e) => { state.autoTab = e.target.checked; });
    document.querySelectorAll("[data-layer]").forEach((box) => {
      box.checked = state.layers[box.dataset.layer];
      box.addEventListener("change", () => { state.layers[box.dataset.layer] = box.checked; showFrame(state.k, false); });
    });
    $("#clean").addEventListener("click", () => { document.body.classList.toggle("clean"); fit(); });
    document.addEventListener("keydown", (e) => {
      if (e.target.closest("input, select")) return;
      if (e.key === "ArrowRight") { play(false); showFrame(state.k + 1); }
      else if (e.key === "ArrowLeft") { play(false); showFrame(state.k - 1); }
      else if (e.key === " ") { e.preventDefault(); play(!state.playing); }
      else if (e.key === "Escape" && document.body.classList.contains("clean")) { document.body.classList.remove("clean"); fit(); }
    });
  }

  function fit() {
    const wrap = $(".stage-wrap");
    const scale = wrap.clientWidth / 1920;
    $("#stage").style.transform = `scale(${scale})`;
  }

  async function loadFeed() {
    if (window.FEED) return window.FEED;
    const res = await fetch("feed.json");
    if (!res.ok) throw new Error(`feed.json: HTTP ${res.status}`);
    return res.json();
  }

  async function start() {
    try {
      state.feed = await loadFeed();
    } catch (err) {
      $("#stage").innerHTML = `<p style="padding:40px;font-size:28px">Could not load the game feed (${err.message}). Generate it with <code>chessmetrics feed</code>.</p>`;
      return;
    }
    build();
    wireControls();
    fit();
    window.addEventListener("resize", fit);
    if (location.hash === "#clean") document.body.classList.add("clean");
    setTab("game");
    // open on the first callout of interest so the first frame shows the product working
    const opener = state.feed.frames.find((f) => f.callouts.some((c) => c.kind === "difficulty")) || state.feed.frames[0];
    showFrame(opener.ply);
    fit();
  }

  window.ChessOverlay = { showFrame: (k) => showFrame(k) };
  start();
})();
