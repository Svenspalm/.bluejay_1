"use strict";
// Montana Legislature Bill Tracker. Two pages in one file, switched by the URL hash so Back works:
//   #y=2027                    search page (form, browse by outcome / subject)
//   #y=2027&v=results&q=...    results page (the matching bills, active filters, sort, paging)
// Data: docs/data/<year>/bills.json (live, rebuilt by the Action 3x daily) merged in the browser with the optional
// analysis.json (AI summaries pushed from commandcenter).

const PAGE = 50, STALE_HOURS = 14, POLL_MS = 10 * 60 * 1000;
const OUTCOMES = {
  "Enacted":              {cls: "good",    sym: "✔"},
  "Adopted (resolution)": {cls: "good",    sym: "✓"},
  "In progress":          {cls: "info",    sym: "◐"},
  "Draft request":        {cls: "neutral", sym: "✎"},
  "Vetoed":               {cls: "bad",     sym: "⊘"},
  "Failed":               {cls: "bad",     sym: "✖"},
  "Withdrawn":            {cls: "warn",    sym: "↩"},
  "Other":                {cls: "neutral", sym: "•"},
};
const EMPTY = () => ({q: "", o: "", t: "", p: "", s: "", a: "", fiscal: false, sort: "number"});
const $ = id => document.getElementById(id);
const S = {cfg: null, year: null, data: null, ai: null, status: null, view: "search", shown: PAGE, rows: [], loadedFetch: null, f: EMPTY()};

function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") n.className = v; else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k.startsWith("aria-")) n.setAttribute(k, String(v));
    else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null) n.append(k.nodeType ? k : document.createTextNode(k));
  return n;
}
const fmtDate = d => d ? new Date(d + "T12:00:00").toLocaleDateString("en-US", {month: "short", day: "numeric", year: "numeric"}) : "";
const fmtTime = iso => new Date(iso).toLocaleString("en-US", {timeZone: "America/Denver", month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short"});
const n0 = x => x.toLocaleString("en-US");
const chip = o => { const m = OUTCOMES[o] || OUTCOMES.Other; return el("span", {class: "chip " + m.cls}, m.sym + " " + o); };
const tag = r => r.bill ? r.bill.replace(/^([A-Z]+)/, "$1 ") : r.draft;

async function getJSON(url, optional) {
  const r = await fetch(url, {cache: "no-store"});
  if (!r.ok) { if (optional) return null; throw new Error(url + " " + r.status); }
  return r.json();
}

// ---------- URL <-> state ----------
const HKEYS = {q: "q", o: "o", t: "t", p: "p", s: "s", a: "a", sort: "sort", fiscal: "fn"};
function readHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  S.f = EMPTY();
  for (const [k, hk] of Object.entries(HKEYS)) if (h.has(hk)) S.f[k] = k === "fiscal" ? h.get(hk) === "1" : h.get(hk);
  S.view = h.get("v") === "results" ? "results" : "search";
  return h.get("y") ? +h.get("y") : null;
}
function hashFor() {
  const h = new URLSearchParams();
  h.set("y", S.year);
  if (S.view === "results") h.set("v", "results");
  for (const [k, hk] of Object.entries(HKEYS)) { const v = S.f[k]; if (v && v !== "number") h.set(hk, v === true ? "1" : v); }
  return "#" + h.toString();
}
// push a history entry when the page changes (so Back returns to the form); replace while just editing
function sync(push) { (push ? history.pushState : history.replaceState).call(history, null, "", hashFor()); }

function show(view, {push = true, focus = true} = {}) {
  S.view = view; S.shown = PAGE;
  sync(push);
  render();
  if (focus) { window.scrollTo(0, 0); (view === "results" ? $("res-h") : $("q")).focus({preventScroll: true}); }
}

// ---------- loading ----------
async function loadSession(year, keepShown) {
  S.year = year;
  const [data, ai, status] = await Promise.all([
    getJSON(`data/${year}/bills.json`), getJSON(`data/${year}/analysis.json`, true), getJSON("data/status.json", true)]);
  S.data = data; S.ai = ai; S.status = status;
  S.loadedFetch = status && status.sessions[year] ? status.sessions[year].fetched : null;
  if (!keepShown) S.shown = PAGE;
  buildControls(); render();
}

function buildControls() {
  const rows = S.data.bills;
  const opts = (sel, first, items, cur) => sel.replaceChildren(el("option", {value: ""}, first), ...items.map(([v, label]) => el("option", {value: v, selected: v === cur}, label)));
  const count = fn => { const m = new Map(); rows.forEach(r => [].concat(fn(r)).forEach(v => v && m.set(v, (m.get(v) || 0) + 1))); return m; };
  const order = Object.keys(OUTCOMES);
  opts($("f-outcome"), "All outcomes", [...count(r => r.outcome)].sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0])).map(([v, c]) => [v, `${v} (${n0(c)})`]), S.f.o);
  opts($("f-type"), "All bill types", [...count(r => r.type)].sort().map(([v, c]) => [v, `${v} (${n0(c)})`]), S.f.t);
  opts($("f-party"), "Any party", [...count(r => r.party)].sort().map(([v, c]) => [v, `${v} (${n0(c)})`]), S.f.p);
  opts($("f-subject"), "All subjects", [...count(r => r.subjects)].sort().map(([v, c]) => [v, `${v} (${n0(c)})`]), S.f.s);
  const hasAI = S.ai && S.ai.count > 0;
  $("area-field").hidden = !hasAI;
  if (hasAI) {
    const areas = new Map(); Object.values(S.ai.bills).forEach(a => a.area && areas.set(a.area, (areas.get(a.area) || 0) + 1));
    opts($("f-area"), "All policy areas", [...areas].sort().map(([v, c]) => [v, `${v} (${n0(c)})`]), S.f.a);
  } else S.f.a = "";
  $("q").value = S.f.q; $("f-fiscal").checked = S.f.fiscal; $("sort").value = S.f.sort;
  $("session").replaceChildren(...S.cfg.sessions.map(s => el("option", {value: s.year, selected: s.year === S.year}, `${s.year} (${s.legislature} Legislature)${s.live ? " · live" : ""}`)));
}

// ---------- matching ----------
function hay(r) {
  if (r._h) return r._h;
  const a = S.ai && S.ai.bills[r.bill];
  r._h = [r.bill, r.bill && r.bill.replace(/^([A-Z]+)/, "$1 "), r.draft, r.title, r.sponsor, r.status, r.subjects.join(" "),
          a && [a.one_line, a.summary, a.area].join(" ")].filter(Boolean).join(" ").toLowerCase();
  return r._h;
}
function matches(r, skip) {
  const f = S.f;
  if (skip !== "o" && f.o && r.outcome !== f.o) return false;
  if (f.t && r.type !== f.t) return false;
  if (f.p && r.party !== f.p) return false;
  if (skip !== "s" && f.s && !r.subjects.includes(f.s)) return false;
  if (f.a) { const a = S.ai && S.ai.bills[r.bill]; if (!a || (a.area !== f.a && a.area2 !== f.a)) return false; }
  if (f.fiscal && !r.fiscal_note) return false;
  if (f.q) { const h = hay(r); for (const t of f.q.toLowerCase().split(/\s+/).filter(Boolean)) if (!h.includes(t)) return false; }
  return true;
}
const typeOrder = t => ({HB: 0, SB: 1, HJ: 2, SJ: 3, HR: 4, SR: 5}[t] ?? 9);
const sorters = {
  number: (a, b) => (!a.bill - !b.bill) || typeOrder(a.type) - typeOrder(b.type) || (a.number || 0) - (b.number || 0) || (a.draft || "").localeCompare(b.draft || ""),
  recent: (a, b) => (b.status_date || "").localeCompare(a.status_date || "") || sorters.number(a, b),
  title: (a, b) => a.title.localeCompare(b.title),
};
const FILTER_LABELS = {q: v => `Search: “${v}”`, o: v => `Outcome: ${v}`, t: v => `Type: ${v}`, p: v => `Party: ${v}`, s: v => `Subject: ${v}`, a: v => `Policy area: ${v}`, fiscal: () => "Has a fiscal note"};

// ---------- rendering ----------
function render() {
  $("view-search").hidden = S.view !== "search";
  $("view-results").hidden = S.view !== "results";
  renderStatus();
  if (S.view === "results") renderResults(); else renderSearch();
}

function renderStatus() {
  const d = S.data, cfg = S.cfg.sessions.find(s => s.year === S.year);
  const inProg = d.bills.filter(r => r.outcome === "In progress").length, enacted = d.bills.filter(r => r.outcome === "Enacted").length;
  $("headline").textContent = d.numbered === 0
    ? `${n0(d.drafts)} draft requests filed for the ${cfg.legislature} Legislature. No bills have been introduced yet: bill numbers, sponsors and text appear after it convenes on ${fmtDate(cfg.convenes)}.`
    : `${n0(d.numbered)} bills introduced: ${n0(inProg)} in progress, ${n0(enacted)} enacted.`;
  const ss = (S.status && S.status.sessions[S.year]) || (d.fetched && {fetched: d.fetched}), asof = $("asof");
  asof.replaceChildren();
  const box = $("status-box"); box.classList.remove("stale");
  if (ss) {
    const hrs = (Date.now() - new Date(ss.fetched)) / 36e5, stale = cfg.live && hrs > STALE_HOURS;   // only a live session can go stale
    box.classList.toggle("stale", stale);
    asof.append(`As of ${fmtTime(ss.fetched)} from api.legmt.gov · `,
      cfg.live ? el("span", {class: "chip " + (stale ? "warn" : "good")}, stale ? "⚠ Stale" : "✔ Fresh") : el("span", {class: "chip neutral"}, "Session complete"),
      cfg.live ? " · rechecked three times a day" : " · final record, not rechecked");
  } else asof.append("Fetch time unavailable.");
}

// ----- search page -----
function renderSearch() {
  const d = S.data, f = S.f;
  document.title = "Montana Legislature Bill Tracker";
  const n = d.bills.filter(r => matches(r)).length, any = Object.entries(f).some(([k, v]) => k !== "sort" && v);
  $("go").textContent = !any ? `Show all ${n0(n)} ${d.numbered ? "bills" : "draft requests"}` : n ? `Show ${n0(n)} result${n === 1 ? "" : "s"}` : "No matches: change the search";
  $("go").disabled = any && n === 0;

  // outcome tiles and subject bars are shortcuts: choosing one opens the results page
  const counts = new Map(); d.bills.filter(r => matches(r, "o")).forEach(r => counts.set(r.outcome, (counts.get(r.outcome) || 0) + 1));
  const tiles = $("outcome-tiles"); tiles.replaceChildren();
  for (const o of Object.keys(OUTCOMES)) if (counts.has(o))
    tiles.append(el("button", {type: "button", class: "tile", "aria-label": `Show ${n0(counts.get(o))} ${o} bills`, onclick: () => { S.f.o = o; $("f-outcome").value = o; show("results"); }},
      el("span", {class: "n"}, n0(counts.get(o))), el("span", {class: "l"}, OUTCOMES[o].sym + " " + o)));
  if (!counts.size) tiles.append(el("p", {class: "hint"}, "No bills match the filters above."));

  const sc = new Map(); d.bills.filter(r => matches(r, "s")).forEach(r => r.subjects.forEach(s => sc.set(s, (sc.get(s) || 0) + 1)));
  const top = [...sc].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 12);
  const max = Math.max(1, ...top.map(t => t[1])), bars = $("subject-bars"); bars.replaceChildren();
  top.forEach(([s, c]) => bars.append(el("li", {}, el("button", {type: "button", "aria-label": `Show ${n0(c)} bills on ${s}`,
    onclick: () => { S.f.s = s; $("f-subject").value = s; show("results"); }},
    el("span", {text: s}), el("span", {class: "track", "aria-hidden": "true"}, el("span", {class: "fill", style: `width:${(c / max * 100).toFixed(1)}%`})), el("span", {class: "v", text: n0(c)})))));
  if (!top.length) bars.append(el("li", {class: "hint", text: "No subjects for the current filters."}));
}

// ----- results page -----
function renderResults() {
  const d = S.data, f = S.f;
  S.rows = d.bills.filter(r => matches(r)).sort(sorters[f.sort]);
  document.title = `${S.rows.length} result${S.rows.length === 1 ? "" : "s"} · Montana Legislature Bill Tracker`;

  // what was searched, each removable
  const act = $("active"); act.replaceChildren();
  for (const [k, lab] of Object.entries(FILTER_LABELS)) if (f[k]) {
    const text = lab(f[k]);
    act.append(el("li", {}, el("span", {class: "pill"}, text,
      el("button", {type: "button", class: "x", "aria-label": `Remove ${text}`, onclick: () => { f[k] = k === "fiscal" ? false : ""; buildControls(); show("results", {push: false, focus: false}); $("res-h").focus({preventScroll: true}); }}, "×"))));
  }
  if (!act.children.length) act.append(el("li", {class: "hint"}, "No filters: showing every bill."));

  $("sort").value = f.sort;
  const total = d.bills.length, shown = Math.min(S.shown, S.rows.length);
  $("count").textContent = !S.rows.length ? "No bills match this search."
    : `${n0(S.rows.length)} of ${n0(total)} ${d.numbered ? "bills" : "draft requests"} match. Showing ${n0(shown)}.`;

  const body = $("rows"); body.replaceChildren();
  if (!S.rows.length) body.append(el("tr", {}, el("td", {colspan: 5}, "Nothing matches. Remove a filter above, use fewer words, or choose Edit search.")));
  S.rows.slice(0, S.shown).forEach(r => {
    const a = S.ai && S.ai.bills[r.bill];
    body.append(el("tr", {},
      el("td", {class: "tag", "data-label": "Bill"}, tag(r)),
      el("td", {"data-label": "Title"}, el("button", {type: "button", class: "link", onclick: () => openDetail(r)}, r.title || "(untitled)"),
        a && a.one_line ? el("div", {class: "sub"}, a.one_line + " (AI)") : null),
      el("td", {"data-label": "Sponsor"}, r.sponsor ? `${r.sponsor}${r.party ? " (" + r.party + ")" : ""}` : "Not yet assigned"),
      el("td", {"data-label": "Latest action"}, r.status || "—", r.status_date ? el("div", {class: "sub"}, fmtDate(r.status_date)) : null),
      el("td", {"data-label": "Outcome"}, chip(r.outcome))));
  });
  $("more").hidden = S.rows.length <= S.shown;
}

// ----- detail dialog -----
function openDetail(r) {
  const a = S.ai && S.ai.bills[r.bill];
  $("d-title").textContent = `${tag(r)}: ${r.title}`;
  const row = (k, v) => v ? [el("dt", {text: k}), el("dd", {}, v)] : [];
  $("d-body").replaceChildren(el("div", {},
    el("dl", {},
      row("Outcome", chip(r.outcome)), row("Latest action", r.status ? `${r.status}${r.status_date ? " (" + fmtDate(r.status_date) + ")" : ""}` : ""),
      row("Sponsor", r.sponsor ? `${r.sponsor}${r.party ? " (" + r.party + ")" : ""}` : ""), row("Chamber", r.chamber && r.chamber[0] + r.chamber.slice(1).toLowerCase()),
      row("Draft number", r.draft), row("Session law chapter", r.chapter && String(r.chapter)),
      row("Official subjects", r.subjects.join("; ")),
      row("Notes", [r.fiscal_note && "Has a fiscal note", r.legal_note && "Has a legal note"].filter(Boolean).join("; "))),
    r.bill ? el("p", {}, el("a", {href: "https://bills.legmt.gov/", target: "_blank", rel: "noopener"}, "Open bills.legmt.gov"), ` and search for ${r.bill} to read the official text.`) : el("p", {class: "hint"}, "This is a draft request. It has no bill number, sponsor or text until it is introduced."),
    a ? aiBlock(a) : null,
    r.history && r.history.length ? [el("h3", {text: "Action history (newest first)"}), el("ol", {class: "hist", reversed: true}, [...r.history].reverse().map(([d, s]) => el("li", {}, `${fmtDate(d)}: ${s}`)))] : null));
  $("detail").showModal();
}
function aiBlock(a) {
  const list = items => items && items.length ? el("ul", {}, items.map(x => el("li", {text: x}))) : null;
  return el("section", {class: "ai", "aria-label": "AI-generated summary"},
    el("h3", {text: "AI-generated summary"}), el("p", {class: "hint"}, `Written by ${S.ai.model || "a local AI model"}. It can be wrong; check the bill text.`),
    a.one_line ? el("p", {class: "tagline", text: a.one_line}) : null, a.summary ? el("p", {text: a.summary}) : null,
    a.changes && a.changes.length ? [el("h3", {text: "What it changes"}), list(a.changes)] : null,
    a.affected && a.affected.length ? [el("h3", {text: "Who is affected"}), list(a.affected)] : null,
    el("dl", {}, [["Policy area", a.area], ["Fiscal impact", a.fiscal && `${a.fiscal}${a.fiscal_dir && a.fiscal_dir !== "none" ? ", " + a.fiscal_dir : ""}`], ["Effective", a.effective],
      ["Key numbers", (a.numbers || []).join("; ")]].flatMap(([k, v]) => v ? [el("dt", {text: k}), el("dd", {text: v})] : [])));
}

// ---------- wiring ----------
function bind() {
  // on the search page, fields only edit the pending search; the button opens the results page
  const edit = (id, k) => $(id).addEventListener("change", e => { S.f[k] = e.target.type === "checkbox" ? e.target.checked : e.target.value; sync(false); renderSearch(); });
  edit("f-outcome", "o"); edit("f-type", "t"); edit("f-party", "p"); edit("f-subject", "s"); edit("f-area", "a"); edit("f-fiscal", "fiscal");
  $("q").addEventListener("input", e => { S.f.q = e.target.value.trim(); sync(false); renderSearch(); });
  $("search-form").addEventListener("submit", e => { e.preventDefault(); if (!$("go").disabled) show("results"); });
  $("clear").addEventListener("click", () => { const sort = S.f.sort; S.f = EMPTY(); S.f.sort = sort; buildControls(); sync(false); renderSearch(); $("q").focus(); });

  $("sort").addEventListener("change", e => { S.f.sort = e.target.value; S.shown = PAGE; sync(false); renderResults(); });
  $("more").addEventListener("click", () => { S.shown += PAGE; renderResults(); });
  $("edit-search").addEventListener("click", () => { buildControls(); show("search"); });
  $("new-search").addEventListener("click", () => { S.f = EMPTY(); buildControls(); show("search"); });
  $("home-link").addEventListener("click", e => { e.preventDefault(); S.f = EMPTY(); buildControls(); show("search"); });

  $("session").addEventListener("change", e => { S.f = EMPTY(); S.view = "search"; loadSession(+e.target.value).then(() => sync(true)).catch(showError); });
  $("d-close").addEventListener("click", () => $("detail").close());
  $("detail").addEventListener("click", e => { if (e.target === $("detail")) $("detail").close(); });
  window.addEventListener("popstate", async () => {      // Back / Forward between the two pages
    const y = readHash();
    if (y && y !== S.year && S.cfg.sessions.some(s => s.year === y)) await loadSession(y); else { buildControls(); render(); }
  });
}
const showError = e => { $("headline").textContent = "Could not load the data. Reload the page; if it keeps failing the refresh may be down."; console.error(e); };

async function poll() {
  try {
    const st = await getJSON("data/status.json", true);
    const f = st && st.sessions[S.year] && st.sessions[S.year].fetched;
    if (f && f !== S.loadedFetch) { await loadSession(S.year, true); return; }
    S.status = st; renderStatus();   // re-evaluate the Fresh/Stale chip as time passes
  } catch (e) { /* offline: keep what is on screen */ }
}

(async function init() {
  try {
    S.cfg = await getJSON("data/sessions.json");
    const y = readHash(), known = S.cfg.sessions.map(s => s.year);
    bind();
    await loadSession(known.includes(y) ? y : S.cfg.default);
    sync(false);
    setInterval(poll, POLL_MS);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  } catch (e) { showError(e); }
})();
