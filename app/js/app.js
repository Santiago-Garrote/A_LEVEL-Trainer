import * as db from "./db.js";
import * as srs from "./srs.js";
import * as ai from "./ai.js";
import { importPack, deletePack, install, fetchCatalog, source as packSource, DEFAULT_SOURCE } from "./packs.js";

const main = document.getElementById("main");
const SESSION_NAMES = { m: "Feb/Mar", s: "May/Jun", w: "Oct/Nov" };

// ------------------------------------------------------------------ helpers

/** Tiny DOM builder: h("div.card#id", {onclick}, child, ...) */
function h(sel, attrs, ...children) {
  if (attrs == null || typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs)) {
    children.unshift(attrs);
    attrs = {};
  }
  const [, tag, rest] = sel.match(/^([a-z0-9]+)(.*)$/i);
  const el = document.createElement(tag);
  for (const [, kind, name] of rest.matchAll(/([.#])([\w-]+)/g)) {
    if (kind === ".") el.classList.add(name);
    else el.id = name;
  }
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k in el && k !== "list") el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function render(...nodes) {
  main.replaceChildren(...nodes.flat(Infinity).filter((n) => n != null && n !== false));
  window.scrollTo(0, 0);
}

let toastTimer;
function toast(msg, ms = 3000) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}

const pct = (x) => `${Math.round(x * 100)}%`;
const shuffle = (a) => {
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
};
const paperLabel = (q) => `${SESSION_NAMES[q.session]} ${q.year} · Paper ${q.variant}`;
const byPaperOrder = (a, b) => a.id.localeCompare(b.id);

function bar(ratio, label) {
  const cls = ratio == null ? "" : ratio >= 0.75 ? "good" : ratio >= 0.5 ? "ok" : "bad";
  return h("div.bar", { title: label ?? "" },
    h(`div.fill.${cls || "none"}`, { style: { width: ratio == null ? "0" : pct(ratio) } }));
}

async function packs() {
  return (await db.all("packs")).sort((a, b) => a.name.localeCompare(b.name));
}

async function questionsFor(code) {
  return (await db.byIndex("questions", "subject", code)).sort(byPaperOrder);
}

// ------------------------------------------------------------------ home

async function home() {
  const list = await packs();
  const cards = [];
  for (const p of list) {
    const states = await srs.states(p.code);
    const vals = [...states.values()];
    const due = vals.filter((s) => srs.isDue(s)).length;
    const avg = vals.length ? vals.reduce((a, s) => a + s.last, 0) / vals.length : null;
    cards.push(h("section.card.subject",
      h("div.row",
        h("div",
          h("h2", p.name),
          h("div.muted", `${p.code} · ${p.count} questions · ${vals.length} attempted`)),
        avg != null && h("div.big", pct(avg))),
      bar(vals.length / p.count, "coverage"),
      h("div.actions",
        h("a.btn.primary", { href: `#/practice/${p.code}` }, "Practice"),
        due > 0 && h("button.btn", { onclick: () => startSession(p.code, { pool: "due", count: 20 }) }, `Review ${due} due`),
        h("a.btn", { href: `#/browse/${p.code}` }, "Browse"))));
  }

  render(
    cards.length > 0 && h("div.grid", cards),
    cards.length > 0 && h("div.actions.center", h("a.btn", { href: "#/subjects" }, "Add or update subjects")),
    guide(cards.length === 0),
  );
}

// ------------------------------------------------------------------ getting started guide

// Chrome/Edge/Android offer an install prompt we can trigger from a button; iOS never does
let installPrompt = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  installPrompt = e;
});

const runningAsApp = () => matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;

function installStep() {
  if (runningAsApp()) return null;
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  return h("li",
    h("strong", "Install the app. "),
    ios ? "In Safari, tap the Share button, then Add to Home Screen."
      : "Use your browser menu → Install app (or Add to Home screen).",
    " It then opens like a normal app and works without internet.",
    installPrompt && h("div.actions", h("button.btn.small", { onclick: async () => {
      installPrompt.prompt();
      await installPrompt.userChoice;
      installPrompt = null;
      home();
    } }, "Install now")));
}

function guide(start) {
  const steps = h("ol.steps",
    installStep(),
    h("li",
      h("strong", "Add your subjects. "),
      "Each subject is a .zip file (like 9702.zip) from whoever set this up for you. Save it on this device, then go to ",
      h("a", { href: "#/subjects" }, "Subjects"), " → Import .zip files (or install it from the list there, if one is shown). It takes a minute and is stored on this device only."),
    h("li",
      h("strong", "Practice. "),
      "Tap Practice on a subject and choose papers, topics and years, or Start paper to sit a full timed past paper. ",
      "Multiple choice is marked instantly. For written questions, work on paper, then open the mark scheme and give yourself the marks you earned."),
    h("li",
      h("strong", "Come back for reviews. "),
      "Questions you get wrong return the next day; ones you get right come back later and later. When some are due, a Review button appears here. A few minutes a day works best."),
    h("li",
      h("strong", "Track progress. "),
      h("a", { href: "#/progress" }, "Progress"), " shows your scores by paper and topic so you can see what to work on. ",
      "It's saved on this device only. To move it to another device, use Settings → Export progress and Import progress."),
    h("li",
      h("strong", "Optional: AI marking. "),
      "Create your own key at openrouter.ai/keys and paste it in ", h("a", { href: "#/settings" }, "Settings"),
      ". Written answers (typed or a photo of your working) are then marked against the mark scheme, about 1¢ per question. Don't use someone else's key."));

  if (start) {
    return h("section.card",
      h("h2", "Getting started"),
      steps,
      h("div.actions", h("a.btn.primary", { href: "#/subjects" }, "Add subjects")));
  }
  return h("details.card", { style: { marginTop: "16px" } }, h("summary", "How to use this app"), steps);
}

// ------------------------------------------------------------------ subjects (install / update / remove)

let installing = null; // code of the pack being installed, one at a time

async function subjects() {
  const src = await packSource();
  const installed = new Map((await packs()).map((p) => [p.code, p]));
  let catalog = null, error = null;
  try {
    catalog = await fetchCatalog(src);
  } catch (err) {
    error = err;
  }
  // only `nix run .#serve` on the laptop can convert links; static hosting has no /api
  const convert = await fetch("api/status", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null)).then((j) => j?.convert === true ? convertCard() : null).catch(() => null);

  const mb = (b) => `${(b / 1e6).toFixed(0)} MB`;
  const cardFor = (entry, have) => {
    const status = h("div.muted.small");
    const fill = h("div.fill", { style: { width: "0" } });
    const progress = h("div.bar", { hidden: true }, fill);
    const update = have && entry && have.built < entry.built;
    const go = async (btn) => {
      if (installing) return toast(`Wait for ${installing} to finish`);
      installing = entry.code;
      btn.disabled = true;
      progress.hidden = false;
      try {
        const r = await install(entry, src, (phase, ratio) => {
          status.textContent = `${phase}… ${pct(ratio)}`;
          fill.style.width = pct(ratio);
        });
        toast(`${r.name}: ${r.count} questions ready`);
      } catch (err) {
        toast(`Install failed: ${err.message}`, 6000);
      } finally {
        installing = null;
      }
      subjects();
    };
    const info = entry
      ? `${entry.count} questions · ${entry.papers} papers · ${entry.years.join("–")} · ${mb(entry.bytes)}`
      : `${have.count} questions · imported from file`;
    return h("section.card",
      h("div.row",
        h("div", h("h2", entry?.name ?? have.name), h("div.muted", `${entry?.code ?? have.code} · ${info}`)),
        have && h("span.tag", update ? "Update available" : "Installed")),
      entry && h("div.tags", Object.values(entry.components).map((c) => h("span.tag", c))),
      progress, status,
      h("div.actions",
        entry && (!have || update) && h("button.btn.primary", { onclick: (e) => go(e.target) }, update ? "Update" : "Install"),
        have && h("button.btn", { onclick: async () => {
          if (!confirm(`Remove ${have.name} questions from this device? Your progress is kept.`)) return;
          await deletePack(have.code);
          subjects();
        } }, "Remove")));
  };

  const listed = new Set((catalog ?? []).map((e) => e.code));
  const extra = [...installed.values()].filter((p) => !listed.has(p.code));

  // importing zip files by hand: the main way in when the app is hosted without packs (e.g. GitHub Pages)
  const importStatus = h("div.muted.small");
  const importFill = h("div.fill", { style: { width: "0" } });
  const importBar = h("div.bar", { hidden: true }, importFill);
  const fileInput = h("input", {
    type: "file", accept: ".zip,application/zip", multiple: true, hidden: true,
    onchange: async (e) => {
      if (installing) return toast(`Wait for ${installing} to finish`);
      const files = [...e.target.files];
      importBar.hidden = false;
      for (const f of files) {
        installing = f.name;
        try {
          const r = await importPack(f, (ratio) => {
            importStatus.textContent = `Importing ${f.name}… ${pct(ratio)}`;
            importFill.style.width = pct(ratio);
          });
          toast(`${r.name}: ${r.count} questions ready`);
        } catch (err) {
          toast(`${f.name}: ${err.message}`, 6000);
        } finally {
          installing = null;
        }
      }
      subjects();
    },
  });
  const importButton = (label, primary) =>
    h(primary ? "button.btn.primary" : "button.btn", { onclick: () => fileInput.click() }, label);

  const srcInput = h("input", { value: src, placeholder: DEFAULT_SOURCE });
  const customSource = src !== DEFAULT_SOURCE;

  render(
    h("h1", "Subjects"),
    error && h("section.card",
      h("h2", "Import your subject files"),
      h("p", "This copy of the app doesn't include any papers. Each subject comes as a .zip file (like 9702.zip) built on your laptop. Copy those files to this device, then import them here. They're stored on this device and work offline."),
      h("div.actions", importButton("Import .zip files", true), fileInput),
      importBar, importStatus,
      customSource && h("p.muted.small", `Couldn't load the subject list from ${src}: ${error.message}`)),
    catalog && !catalog.length && h("p.muted", "The catalog is empty. Build a subject with `nix run .#papers -- all <code>`."),
    catalog && h("div.grid", catalog.map((e) => cardFor(e, installed.get(e.code)))),
    extra.length > 0 && h("div.grid", { style: { marginTop: "16px" } }, extra.map((p) => cardFor(null, p))),
    catalog && h("div", importBar, importStatus),
    convert?.card,
    h("details.card", { style: { marginTop: "16px" } },
      h("summary", catalog ? "Other ways to add subjects" : "Advanced: download from a pack source"),
      h("p.muted.small", "A pack source is a folder or HTTPS URL holding index.json and the .zip packs (on another site it must allow CORS). `nix run .#serve` provides one on your laptop."),
      h("label", "Pack source", srcInput),
      h("div.actions",
        h("button.btn", { onclick: async () => {
          await db.setSetting("packSource", srcInput.value.trim() || DEFAULT_SOURCE);
          subjects();
        } }, "Save & reload"),
        catalog && [importButton("Import .zip file"), fileInput])),
  );
  convert?.poll(true);
}

/** Paste PapaCambridge links; the laptop server downloads, splits and packs them. */
function convertCard() {
  const links = h("textarea", { rows: 3, placeholder: "https://pastpapers.papacambridge.com/papers/caie/as-and-a-level-physics-9702-2023-may-june" });
  const log = h("pre.log", { hidden: true });
  const status = h("div.muted.small");
  const button = h("button.btn.primary", { onclick: async () => {
    const list = links.value.split(/\s+/).filter(Boolean);
    if (!list.length) return toast("Paste at least one link");
    const res = await fetch("api/convert", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ links: list }),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) return toast(j.error ?? `HTTP ${res.status}`, 6000);
    links.value = "";
    poll(false);
  } }, "Convert");

  let polling = false;
  async function poll(initial) {
    if (polling) return;
    polling = true;
    let seenRunning = false;
    try {
      while (log.isConnected) { // stops when the page is left or re-rendered
        const j = await fetch("api/convert", { cache: "no-store" }).then((r) => r.json());
        button.disabled = j.running;
        if (j.running || !initial) {
          log.hidden = !j.log.length;
          log.textContent = j.log.slice(-40).join("\n");
          log.scrollTop = log.scrollHeight;
        }
        if (j.running) {
          seenRunning = true;
          status.textContent = "Converting on your laptop… you can leave this page, it keeps going.";
        } else {
          if (seenRunning || !initial) {
            if (j.exit === 0) {
              toast("Conversion finished: install or update the subject above", 6000);
              return subjects();
            }
            status.textContent = `Conversion failed (exit ${j.exit}), see the log above.`;
          }
          return;
        }
        await new Promise((r) => setTimeout(r, 1500));
      }
    } catch (err) {
      status.textContent = `Lost contact with the laptop server: ${err.message}`;
    } finally {
      polling = false;
    }
  }

  const card = h("section.card", { style: { marginTop: "16px" } },
    h("h2", "Convert from PapaCambridge"),
    h("p.muted", "Paste links, one per line: a paper PDF, a session page (like Physics May/June 2023) or a whole subject page (2018 onwards). Your laptop downloads the papers and their mark schemes and builds the pack, then it appears above to install."),
    links,
    h("div.actions", button),
    log, status);
  return { card, poll };
}

// ------------------------------------------------------------------ practice setup

async function practice(code) {
  const pack = await db.get("packs", code);
  if (!pack) return (location.hash = "#/");
  const qs = await questionsFor(code);
  const states = await srs.states(code);
  const comps = Object.keys(pack.components).filter((c) => qs.some((q) => String(q.component) === c));
  const years = [...new Set(qs.map((q) => q.year))].sort();
  const papers = [...new Set(qs.map((q) => q.paper))].sort().reverse();

  const sel = { comps: new Set(comps), topics: new Set(), pool: "all", count: 10, order: "random" };
  const counter = h("span.muted");
  const topicBox = h("div.chips");

  function filtered() {
    return qs.filter((q) =>
      sel.comps.has(String(q.component)) &&
      q.year >= +yearFrom.value && q.year <= +yearTo.value &&
      (sel.topics.size === 0 || q.topics.some((t) => sel.topics.has(t))) &&
      ({ all: true, unseen: !states.has(q.id), due: srs.isDue(states.get(q.id)), weak: srs.isWeak(states.get(q.id)) })[sel.pool]);
  }
  const update = () => (counter.textContent = `${filtered().length} matching questions`);

  function drawTopics() {
    const topics = pack.topics.filter((t) => t.components.some((c) => sel.comps.has(String(c))));
    topicBox.replaceChildren(...topics.map((t) => {
      const n = qs.filter((q) => q.topics.includes(t.id)).length;
      return chip(t.name + ` (${n})`, sel.topics.has(t.id), (on) => { on ? sel.topics.add(t.id) : sel.topics.delete(t.id); update(); });
    }));
    if (!topics.length) topicBox.append(h("span.muted", "No topic tags for this subject."));
  }

  const yearSelect = (v) => h("select", { onchange: update }, years.map((y) => h("option", { value: y, selected: y === v }, y)));
  const yearFrom = yearSelect(years[0]);
  const yearTo = yearSelect(years.at(-1));
  let paperPick;

  render(
    h("h1", `${pack.name} practice`),
    h("section.card",
      h("h3", "Papers"),
      h("div.chips", comps.map((c) => chip(pack.components[c], true, (on) => {
        on ? sel.comps.add(c) : sel.comps.delete(c);
        drawTopics(); update();
      }))),
      h("h3", "Topics ", h("span.muted.small", "(none selected = all)")),
      topicBox,
      h("h3", "Years"),
      h("div.row.left", yearFrom, " to ", yearTo),
      h("h3", "Which questions"),
      radios("pool", [["all", "All"], ["unseen", "Not attempted"], ["due", "Due for review"], ["weak", "Weak (<60%)"]], sel.pool, (v) => { sel.pool = v; update(); }),
      h("h3", "How many"),
      radios("count", [[5, "5"], [10, "10"], [20, "20"], [40, "40"], [0, "All"]], sel.count, (v) => (sel.count = +v)),
      h("h3", "Order"),
      radios("order", [["random", "Random"], ["paper", "Paper order"]], sel.order, (v) => (sel.order = v)),
      h("div.actions", counter,
        h("button.btn.primary", { onclick: () => startSession(code, { ids: filtered().map((q) => q.id), count: sel.count, order: sel.order }) }, "Start"))),
    h("section.card",
      h("h3", "Full paper"),
      h("p.muted", "Sit a complete past paper in order, with a timer."),
      h("div.row.left",
        paperPick = h("select", papers.map((p) => {
          const q = qs.find((x) => x.paper === p);
          return h("option", { value: p }, paperLabel(q));
        })),
        h("button.btn", { onclick: () => startSession(code, { paper: paperPick.value }) }, "Start paper"))),
  );

  drawTopics();
  update();
}

function chip(label, on, onchange) {
  const el = h("button.chip", { type: "button", "aria-pressed": String(on) }, label);
  el.addEventListener("click", () => {
    const now = el.getAttribute("aria-pressed") !== "true";
    el.setAttribute("aria-pressed", String(now));
    onchange(now);
  });
  return el;
}

function radios(name, options, value, onchange) {
  return h("div.chips", options.map(([v, label]) =>
    h("label.chip.radio", h("input", { type: "radio", name, value: v, checked: v === value, onchange: () => onchange(v) }), label)));
}

// ------------------------------------------------------------------ sessions

async function startSession(code, { ids, count = 10, order = "random", pool, paper }) {
  const qs = await questionsFor(code);
  let pick;
  if (paper) {
    pick = qs.filter((q) => q.paper === paper);
  } else if (pool === "due") {
    const states = await srs.states(code);
    pick = shuffle(qs.filter((q) => srs.isDue(states.get(q.id)))).slice(0, count);
  } else {
    const set = new Set(ids);
    pick = qs.filter((q) => set.has(q.id));
    if (order === "random") shuffle(pick);
    if (count) pick = pick.slice(0, count);
  }
  if (!pick.length) return toast("No questions match those filters.");
  const marks = pick.reduce((a, q) => a + (q.marks ?? 0), 0);
  const session = {
    code, ids: pick.map((q) => q.id), i: 0, results: [], started: Date.now(),
    // CAIE allows roughly 1.25–1.5 min per mark; used only for full papers
    limit: paper ? Math.round(marks * 1.4) * 60e3 : null,
    title: paper ? paperLabel(pick[0]) : `${pick.length} questions`,
  };
  await db.setSetting("session", session);
  location.hash = "#/session";
}

let timerHandle;
async function session() {
  const s = await db.setting("session");
  if (!s) return (location.hash = "#/");
  if (s.i >= s.ids.length) return summary(s);
  const q = await db.get("questions", s.ids[s.i]);
  if (!q) { s.i++; await db.setSetting("session", s); return session(); }

  const clock = h("span.clock");
  clearInterval(timerHandle);
  const tick = () => {
    const el = Date.now() - s.started;
    const show = s.limit ? s.limit - el : el;
    const m = Math.floor(Math.abs(show) / 60e3), sec = Math.floor((Math.abs(show) % 60e3) / 1e3);
    clock.textContent = `${show < 0 ? "-" : ""}${m}:${String(sec).padStart(2, "0")}`;
    clock.classList.toggle("over", show < 0);
  };
  tick();
  timerHandle = setInterval(() => (main.contains(clock) ? tick() : clearInterval(timerHandle)), 1000);

  const next = async (result) => {
    if (result) s.results.push({ qid: q.id, ...result });
    s.i++;
    await db.setSetting("session", s);
    session();
  };

  render(
    h("div.sessionbar",
      h("span", `${s.title} · ${s.i + 1}/${s.ids.length}`),
      clock,
      h("button.btn.small", { onclick: () => next(null) }, "Skip"),
      h("button.btn.small", { onclick: () => summary(s) }, "End")),
    h("div.progress", h("div", { style: { width: pct(s.i / s.ids.length) } })),
    await questionView(q, next),
  );
}

/** Question card with answering + marking. onDone({score, max, method}) is called once marked. */
async function questionView(q, onDone) {
  const card = h("section.card.question");
  const header = h("div.qhead",
    h("div", h("strong", `Q${q.number}`), " ", h("span.muted", `${q.subject} · ${paperLabel(q)}`)),
    h("div.muted", q.marks ? `${q.marks} mark${q.marks > 1 ? "s" : ""}` : ""));
  const img = h("img.qimg", { src: await db.imageURL(q.image), alt: q.text.slice(0, 300), onclick: (e) => e.target.classList.toggle("zoom") });
  card.append(header, img);
  if (q.topics.length) card.append(h("div.tags", q.topics.map((t) => h("span.tag", t))));

  const area = h("div.answer");
  card.append(area);
  if (q.type === "mcq") mcq(q, area, onDone);
  else await structured(q, area, onDone);
  return card;
}

function mcq(q, area, onDone) {
  const buttons = ["A", "B", "C", "D"].map((L) => h("button.opt", { onclick: () => choose(L) }, L));
  area.append(h("div.opts", buttons));
  function choose(L) {
    const correct = L === q.answer;
    buttons.forEach((b) => {
      b.disabled = true;
      if (b.textContent === q.answer) b.classList.add("right");
      else if (b.textContent === L) b.classList.add("wrong");
    });
    const result = { score: correct ? 1 : 0, max: 1, method: "auto" };
    srs.record({ q, ...result, answer: L });
    area.append(
      h(`p.verdict.${correct ? "good" : "bad"}`, correct ? "Correct" : `Incorrect, the answer is ${q.answer}`),
      onDone && h("div.actions", h("button.btn.primary", { onclick: () => onDone(result) }, "Next")));
  }
}

async function structured(q, area, onDone) {
  const useAI = await ai.enabled();
  const photos = [];
  const thumbs = h("div.thumbs");
  const text = h("textarea", { rows: 5, placeholder: "Type your answer (optional): label parts like (a)(i)…" });
  const photoInput = h("input", {
    type: "file", accept: "image/*", capture: "environment", multiple: true, hidden: true,
    onchange: async (e) => {
      for (const f of e.target.files) {
        const small = await ai.shrinkPhoto(f);
        photos.push(small);
        thumbs.append(h("img", { src: URL.createObjectURL(small), alt: "your working" }));
      }
    },
  });
  const controls = h("div.actions",
    useAI && h("button.btn", { onclick: () => photoInput.click() }, "Add photo of working"), photoInput,
    useAI && h("button.btn.primary", { onclick: markAI }, "Mark with AI"),
    h(`button.btn${useAI ? "" : ".primary"}`, { onclick: () => selfMark() }, useAI ? "Self-mark" : "Show mark scheme"));
  area.append(text, thumbs, controls);

  async function showMS() {
    if (!q.msImage) return h("p.muted", "Mark scheme not found for this question, so mark it against your notes or the original PDF.");
    return h("div", h("h3", "Mark scheme"), h("img.qimg.ms", { src: await db.imageURL(q.msImage), alt: "mark scheme", onclick: (e) => e.target.classList.toggle("zoom") }));
  }

  async function selfMark(preset, method = "self") {
    controls.remove();
    text.readOnly = true;
    const max = q.marks ?? 10;
    let score = preset ?? null;
    const save = h("button.btn.primary", { disabled: score == null, onclick: async () => {
      save.disabled = true;
      const result = { score, max, method };
      await srs.record({ q, ...result, answer: text.value });
      if (onDone) onDone(result);
      else toast("Saved");
    } }, onDone ? "Save & next" : "Save");
    const picks = h("div.scores", Array.from({ length: max + 1 }, (_, n) => {
      const b = h("button.score", { "aria-pressed": String(n === score), onclick: () => {
        score = n;
        picks.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
        save.disabled = false;
      } }, n);
      return b;
    }));
    area.append(await showMS(), h("h3", `Your score (out of ${max})`), picks, h("div.actions", save));
  }

  async function markAI() {
    if (!text.value.trim() && !photos.length) return toast("Type an answer or add a photo of your working first.");
    controls.querySelectorAll("button").forEach((b) => (b.disabled = true));
    const wait = h("p.muted", "Marking…");
    area.append(wait);
    try {
      const r = await ai.mark(q, text.value, photos);
      wait.remove();
      area.append(h("div.aires",
        h("div.row", h("h3", "AI marking"), h("div.big", `${r.score}/${r.max}`)),
        h("table", h("tbody", r.points.map((p) => h("tr",
          h("td", p.part), h("td.num", `${p.awarded}/${p.available}`), h("td", p.comment))))),
        h("p", r.feedback),
        h("p.muted.small", `${r.model}${r.cost != null ? ` · $${r.cost.toFixed(4)}` : ""} · AI can be wrong, so check against the mark scheme and adjust if needed.`)));
      await selfMark(r.score, "ai");
    } catch (err) {
      wait.remove();
      toast(err.message, 6000);
      controls.querySelectorAll("button").forEach((b) => (b.disabled = false));
    }
  }
}

async function summary(s) {
  clearInterval(timerHandle);
  await db.setSetting("session", null);
  const got = s.results.reduce((a, r) => a + r.score, 0);
  const max = s.results.reduce((a, r) => a + r.max, 0);
  const qs = await Promise.all(s.results.map((r) => db.get("questions", r.qid)));
  const mins = Math.round((Date.now() - s.started) / 60e3);
  render(
    h("h1", "Session complete"),
    h("section.card",
      h("div.row", h("div", h("div.muted", `${s.results.length} answered · ${mins} min`)), h("div.big", max ? `${got}/${max} · ${pct(got / max)}` : "-")),
      max ? bar(got / max) : null,
      h("table.results", h("tbody", s.results.map((r, i) => h("tr",
        h("td", h("a", { href: `#/q/${r.qid}` }, `Q${qs[i]?.number}`), " ", h("span.muted.small", qs[i] ? paperLabel(qs[i]) : "")),
        h("td.num", `${r.score}/${r.max}`),
        h("td", bar(r.max ? r.score / r.max : 0))))))),
    h("div.actions.center",
      h("a.btn.primary", { href: `#/practice/${s.code}` }, "Practice again"),
      h("a.btn", { href: "#/" }, "Home")),
  );
}

// ------------------------------------------------------------------ browse & single question

async function browse(code) {
  const pack = await db.get("packs", code);
  if (!pack) return (location.hash = "#/");
  const qs = await questionsFor(code);
  const states = await srs.states(code);
  const byPaper = Map.groupBy(qs, (q) => q.paper);
  const papers = [...byPaper.keys()].sort().reverse();
  render(
    h("h1", `${pack.name}: all papers`),
    papers.map((p) => {
      const list = byPaper.get(p);
      return h("details.card",
        h("summary", h("strong", paperLabel(list[0])), " ", h("span.muted", `${list.length} questions · ${pack.components[list[0].component] ?? ""}`)),
        h("div.qgrid", list.map((q) => {
          const st = states.get(q.id);
          const cls = !st ? "" : st.last >= 0.75 ? ".good" : st.last >= 0.5 ? ".ok" : ".bad";
          return h(`a.qcell${cls}`, { href: `#/q/${q.id}`, title: q.topics.join(", ") }, q.number);
        })));
    }),
  );
}

async function single(id) {
  const q = await db.get("questions", id);
  if (!q) return (location.hash = "#/");
  const attempts = (await db.byIndex("attempts", "qid", id)).sort((a, b) => b.ts - a.ts);
  render(
    h("div.actions", h("a.btn.small", { href: `#/browse/${q.subject}` }, "← All papers")),
    await questionView(q, null),
    attempts.length > 0 && h("section.card",
      h("h3", "Previous attempts"),
      h("table", h("tbody", attempts.map((a) => h("tr",
        h("td", new Date(a.ts).toLocaleDateString()), h("td.num", `${a.score}/${a.max}`), h("td.muted", a.method)))))),
  );
}

// ------------------------------------------------------------------ progress

async function progress() {
  const list = await packs();
  if (!list.length) return render(h("p.muted", "Import a subject first."));
  const attempts = await db.all("attempts");
  const sections = [];
  for (const p of list) {
    const qs = await questionsFor(p.code);
    const states = await srs.states(p.code);
    const group = (keyFn, names) => {
      const rows = new Map();
      for (const q of qs) for (const k of [keyFn(q)].flat()) {
        const r = rows.get(k) ?? { n: 0, done: 0, sum: 0 };
        r.n++;
        const st = states.get(q.id);
        if (st) { r.done++; r.sum += st.last; }
        rows.set(k, r);
      }
      return h("table.stats", h("tbody", [...rows].map(([k, r]) => h("tr",
        h("td", names(k)),
        h("td.num.muted", `${r.done}/${r.n}`),
        h("td.wide", bar(r.done ? r.sum / r.done : null)),
        h("td.num", r.done ? pct(r.sum / r.done) : "-")))));
    };
    const topicName = Object.fromEntries(p.topics.map((t) => [t.id, t.name]));
    const recent = attempts.filter((a) => a.subject === p.code);
    const week = recent.filter((a) => a.ts > Date.now() - 7 * 86400e3);
    sections.push(h("section.card",
      h("h2", p.name),
      h("p.muted", `${states.size}/${qs.length} questions attempted · ${recent.length} attempts total · ${week.length} this week · ${[...states.values()].filter((s) => srs.isDue(s)).length} due for review`),
      h("h3", "By paper"),
      group((q) => q.component, (c) => p.components[c] ?? `Paper ${c}`),
      p.topics.length > 0 && h("h3", "By topic ", h("span.muted.small", "(average of latest score per question)")),
      p.topics.length > 0 && group((q) => (q.topics.length ? q.topics : ["_none"]), (t) => topicName[t] ?? "Untagged")));
  }
  render(h("h1", "Progress"), sections);
}

// ------------------------------------------------------------------ settings

async function settings() {
  const cfg = await ai.config();
  const key = h("input", { type: "password", value: cfg.key, placeholder: "sk-or-…", autocomplete: "off" });
  const model = h("input", { value: cfg.model, list: "models" });
  const datalist = h("datalist#models");
  const modelInfo = h("p.muted.small");
  let models = [];
  ai.listModels().then((ms) => {
    models = ms;
    datalist.replaceChildren(...ms.filter((m) => m.structured).map((m) =>
      h("option", { value: m.id }, `${m.name} ($${m.input.toFixed(2)} / $${m.output.toFixed(2)} per 1M)`)));
    showInfo();
  }).catch(() => (modelInfo.textContent = "Couldn't load the model list (offline?). You can still type a model id."));
  const showInfo = () => {
    const m = models.find((x) => x.id === model.value);
    modelInfo.textContent = m
      ? `${m.name}: $${m.input.toFixed(2)} in / $${m.output.toFixed(2)} out per 1M tokens${m.structured ? "" : " · no structured-output support, so JSON may be less reliable"}`
      : "";
  };
  model.addEventListener("input", showInfo);

  const list = await packs();
  const est = await navigator.storage?.estimate?.();
  const backupInput = h("input", { type: "file", accept: ".json", hidden: true, onchange: restore });

  render(
    h("h1", "Settings"),
    h("section.card",
      h("h3", "AI marking (OpenRouter)"),
      h("p.muted", "Optional. With a key, written answers can be marked by AI against the mark scheme. Without one, you mark yourself. MCQs are always marked automatically. Get a key at openrouter.ai/keys. It's stored only on this device."),
      h("label", "API key", key),
      h("label", "Model", model), datalist, modelInfo,
      h("div.actions",
        h("button.btn.primary", { onclick: async () => {
          await db.setSetting("openrouterKey", key.value.trim());
          await db.setSetting("openrouterModel", model.value.trim() || ai.DEFAULT_MODEL);
          toast("Saved");
        } }, "Save"))),
    h("section.card",
      h("h3", "Subjects"),
      h("p.muted", list.length ? list.map((p) => p.name).join(", ") : "None installed."),
      h("div.actions", h("a.btn", { href: "#/subjects" }, "Manage subjects")),
      est && h("p.muted.small", `Storage used: ${(est.usage / 1e6).toFixed(1)} MB of ${(est.quota / 1e6).toFixed(0)} MB available`)),
    h("section.card",
      h("h3", "Progress backup"),
      h("p.muted", "Move your progress between laptop and phone, or keep a backup."),
      h("div.actions",
        h("button.btn", { onclick: backup }, "Export progress"),
        h("button.btn", { onclick: () => backupInput.click() }, "Import progress"), backupInput)),
  );
}

async function backup() {
  const data = { format: 1, exported: new Date().toISOString(), attempts: await db.all("attempts"), srs: await db.all("srs") };
  const a = h("a", { href: URL.createObjectURL(new Blob([JSON.stringify(data)], { type: "application/json" })), download: `alevel-progress-${new Date().toISOString().slice(0, 10)}.json` });
  a.click();
}

async function restore(e) {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    if (data.format !== 1) throw new Error("Unknown backup format");
    // merge: keep newest srs state per question, union of attempts (dedupe by qid+ts)
    const have = new Set((await db.all("attempts")).map((a) => `${a.qid}@${a.ts}`));
    const writes = data.attempts.filter((a) => !have.has(`${a.qid}@${a.ts}`)).map(({ id, ...a }) => ["attempts", a]);
    const local = new Map((await db.all("srs")).map((s) => [s.qid, s]));
    for (const s of data.srs) if (!local.has(s.qid) || (local.get(s.qid).ts ?? 0) < (s.ts ?? 0)) writes.push(["srs", s]);
    if (writes.length) await db.putMany(writes);
    toast(`Merged ${writes.length} records`);
  } catch (err) {
    toast(`Restore failed: ${err.message}`, 6000);
  }
}

// ------------------------------------------------------------------ router

const routes = [
  [/^#?\/?$/, home],
  [/^#\/practice\/(\w+)$/, practice],
  [/^#\/session$/, session],
  [/^#\/browse\/(\w+)$/, browse],
  [/^#\/q\/([\w]+)$/, single],
  [/^#\/progress$/, progress],
  [/^#\/settings$/, settings],
  [/^#\/subjects$/, subjects],
];

async function route() {
  clearInterval(timerHandle);
  for (const [re, view] of routes) {
    const m = location.hash.match(re);
    if (m) {
      try {
        await view(...m.slice(1));
      } catch (err) {
        console.error(err);
        render(h("section.card", h("h2", "Something went wrong"), h("pre", String(err.stack ?? err))));
      }
      return;
    }
  }
  location.hash = "#/";
}

window.addEventListener("hashchange", route);
route();

if ("serviceWorker" in navigator && window.isSecureContext) {
  navigator.serviceWorker.register("sw.js").catch((e) => console.warn("SW registration failed", e));
}
navigator.storage?.persist?.();
