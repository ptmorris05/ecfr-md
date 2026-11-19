// The navigator's behaviour, on the native web platform: no framework, no
// build step. Everything here is an enhancement; every page works without it.

const root = document.documentElement;
const body = document.body;
const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
const reduced = matchMedia("(prefers-reduced-motion: reduce)");
const page = {
  address: body.dataset.address || "",
  cite: body.dataset.cite || "",
  heading: body.dataset.heading || "",
  date: body.dataset.date || "",
  current: body.dataset.current !== "false",
  href: body.dataset.href || location.pathname,
  level: body.dataset.level || "",
  bluebook: body.dataset.bluebook || "",
};
page.title = page.address ? page.address.split("/")[0].split("@")[0] : "";
page.rest = page.href.replace(/^\/(?:current|on\/\d{4}-\d{2}-\d{2})\//, "").split("#")[0];
page.prefix = page.current ? "/current" : `/on/${page.date}`;
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const escapeHtml = (text) =>
  String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const longDate = (iso) => {
  const [y, m, d] = iso.split("-").map(Number);
  return `${MONTHS[m - 1]} ${d}, ${y}`;
};
const scroll = (el, block = "center") => el.scrollIntoView({ block, behavior: reduced.matches ? "auto" : "smooth" });
const typing = (event) => {
  const el = event.target;
  return el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
};
const json = async (url) => {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${response.status} ${url}`);
  return response.json();
};
const whenShown = (fn) => {
  if (document.prerendering) document.addEventListener("prerenderingchange", fn, { once: true });
  else fn();
};
const idle = (fn) => (window.requestIdleCallback ? requestIdleCallback(fn, { timeout: 1500 }) : setTimeout(fn, 60));

// Preferences --------------------------------------------------------------------

const prefs = (() => {
  try {
    return JSON.parse(localStorage.getItem("ecfr-prefs") || "{}") || {};
  } catch {
    return {};
  }
})();
prefs.theme ||= "auto";
prefs.cite ||= "ofr";
if (typeof prefs.size !== "number") prefs.size = 2;

function savePrefs() {
  try {
    localStorage.setItem("ecfr-prefs", JSON.stringify(prefs));
  } catch {}
}

function applyPrefs() {
  if (prefs.theme === "auto") delete root.dataset.theme;
  else root.dataset.theme = prefs.theme;
  root.style.setProperty("--scale", (window.ecfrScales || [])[prefs.size] || 1);
  for (const name of ["dense", "rail", "focus"]) root.classList.toggle(name, !!prefs[name]);
  for (const button of $$("[data-pref]")) button.setAttribute("aria-checked", String(prefs[button.dataset.pref] === button.dataset.value));
  for (const button of $$("[data-toggle]")) button.setAttribute("aria-pressed", String(!!prefs[button.dataset.toggle]));
  if (prefs.rail) Rail.build();
}

function setPref(name, value) {
  prefs[name] = value;
  savePrefs();
  applyPrefs();
}

const toggle = (name) => setPref(name, !prefs[name]);
const step = (delta) => setPref("size", Math.min(5, Math.max(0, prefs.size + delta)));

document.addEventListener("click", (event) => {
  const pref = event.target.closest("[data-pref]");
  if (pref) return setPref(pref.dataset.pref, pref.dataset.value);
  const stepper = event.target.closest("[data-step]");
  if (stepper) return step(Number(stepper.dataset.value));
  const toggler = event.target.closest("[data-toggle]");
  if (toggler) return toggle(toggler.dataset.toggle);
  if (event.target.closest("[data-keys]")) {
    $("#menu")?.hidePopover?.();
    return $("#keys")?.showModal();
  }
  const beside = event.target.closest("[data-beside]");
  if (beside) {
    event.preventDefault();
    Card.hide(true);
    return Split.open(beside.dataset.beside);
  }
});

// Messages and the clipboard ------------------------------------------------------

let toastTimer;
function toast(message) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = message;
  try {
    el.showPopover();
  } catch {}
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    try {
      el.hidePopover();
    } catch {}
  }, 2200);
}

async function copy(text, html, message = "Copied") {
  try {
    if (html && window.ClipboardItem) {
      await navigator.clipboard.write([
        new ClipboardItem({
          "text/plain": new Blob([text], { type: "text/plain" }),
          "text/html": new Blob([html], { type: "text/html" }),
        }),
      ]);
    } else {
      await navigator.clipboard.writeText(text);
    }
  } catch {
    const area = Object.assign(document.createElement("textarea"), { value: text });
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  toast(message);
}

// Citations --------------------------------------------------------------------------

const MARKER = /\(([^()]+)\)/g;

function parseAnchor(id) {
  const found = /^p-(.+?)((?:\([^()]+\))+)$/.exec(id || "");
  if (!found) return null;
  return { section: found[1], markers: [...found[2].matchAll(MARKER)].map((m) => m[1]) };
}

const anchorOf = (section, markers) => `p-${section}${markers.map((m) => `(${m})`).join("")}`;
const year = () => (page.date || new Date().toISOString()).slice(0, 4);

function citation(section, markers = [], end = null, style = prefs.cite) {
  let pin = markers.map((m) => `(${m})`).join("");
  if (end) {
    let same = 0;
    while (same < markers.length && same < end.length - 1 && markers[same] === end[same]) same++;
    pin += "–" + end.slice(same).map((m) => `(${m})`).join("");
  }
  if (style === "bluebook") return `${page.title} C.F.R. § ${section}${pin} (${year()})`;
  return `${page.title} CFR ${section}${pin}`;
}

function sectionCite(el, style = prefs.cite) {
  const section = el?.closest("section.div[data-cite]");
  const cite = section?.dataset.cite || page.cite;
  if (style !== "bluebook") return cite;
  const bluebook = section?.dataset.bluebook || page.bluebook;
  return bluebook || cite;
}

function official(anchor) {
  const found = parseAnchor(anchor);
  const path = found ? `${page.prefix}/title-${page.title}/section-${found.section}` : page.href.split("#")[0];
  return `https://www.ecfr.gov${path}${anchor ? `#${anchor}` : ""}`;
}

function asOf() {
  return page.current ? `eCFR, current as of ${longDate(page.date)}` : `eCFR, as of ${longDate(page.date)}`;
}

function paragraphCitation(el, style = prefs.cite) {
  const found = parseAnchor(el?.id);
  return found ? citation(found.section, found.markers, null, style) : sectionCite(el, style);
}

// Paragraph ranges: #p-164.502(a)(1)-(3) is (a)(1) through (a)(3).

function parseRange(hash) {
  const found = /^p-([^()]+)((?:\([^()]+\))+)-((?:\([^()]+\))+)$/.exec(decodeURIComponent(hash));
  if (!found) return null;
  const start = [...found[2].matchAll(MARKER)].map((m) => m[1]);
  const tail = [...found[3].matchAll(MARKER)].map((m) => m[1]);
  const end = start.slice(0, Math.max(0, start.length - tail.length)).concat(tail);
  return { section: found[1], start, end };
}

function rangeHash(section, start, end) {
  let same = 0;
  while (same < start.length && same < end.length - 1 && start[same] === end[same]) same++;
  return anchorOf(section, start) + "-" + end.slice(same).map((m) => `(${m})`).join("");
}

const paragraphFor = (id) => {
  const el = document.getElementById(id);
  return el ? el.closest(".p") || el : null;
};
const depthOf = (el) => Number((/\bd(\d)\b/.exec(el.className) || [0, 1])[1]);

function paragraphsBetween(first, last) {
  const all = $$("#text .p");
  let i = all.indexOf(first);
  let j = all.indexOf(last);
  if (i < 0 || j < 0) return [];
  if (j < i) [i, j] = [j, i];
  const depth = depthOf(all[j]);
  let k = j + 1;
  while (k < all.length && depthOf(all[k]) > depth) k++;
  return all.slice(i, k);
}

function highlightRange(range, move = true) {
  for (const el of $$(".p.hl")) el.classList.remove("hl");
  const first = paragraphFor(anchorOf(range.section, range.start));
  const last = paragraphFor(anchorOf(range.section, range.end));
  const found = paragraphsBetween(first, last);
  for (const el of found) el.classList.add("hl");
  if (move && found[0]) scroll(found[0], "start");
  return found;
}

function readHash() {
  const hash = location.hash.slice(1);
  const range = parseRange(hash);
  if (range) highlightRange(range);
}
addEventListener("hashchange", readHash);

// Paragraph tools: copy a link or a citation; shift-click a marker for a range.

const Tools = {
  el: null,
  target: null,
  build() {
    const text = $("#text");
    if (!text || !$(".p", text)) return;
    const el = document.createElement("div");
    el.className = "ptools";
    el.innerHTML = `
      <button type="button" data-act="cite" aria-label="Copy citation" title="Copy citation (y)"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M7.5 6.5H5a1.5 1.5 0 0 0-1.5 1.5v2A1.5 1.5 0 0 0 5 11.5h2.5V6.5Zm0 5c0 2-1 3-3 3.5M15.5 6.5H13a1.5 1.5 0 0 0-1.5 1.5v2a1.5 1.5 0 0 0 1.5 1.5h2.5V6.5Zm0 5c0 2-1 3-3 3.5"/></svg></button>
      <button type="button" data-act="link" aria-label="Copy link" title="Copy link to this paragraph"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M8.5 11.5a3 3 0 0 0 4.2 0l2.6-2.6a3 3 0 0 0-4.2-4.2l-.9.9M11.5 8.5a3 3 0 0 0-4.2 0l-2.6 2.6a3 3 0 0 0 4.2 4.2l.9-.9"/></svg></button>`;
    text.append(el);
    this.el = el;
    text.addEventListener("pointerover", (event) => {
      const p = event.target.closest(".p");
      if (p && p !== this.target) this.show(p);
    });
    text.addEventListener("pointerleave", () => this.hide());
    el.addEventListener("click", (event) => {
      const act = event.target.closest("[data-act]")?.dataset.act;
      if (!act || !this.target) return;
      const p = this.target;
      if (act === "link") copy(official(p.id), null, "Link copied");
      else copy(paragraphCitation(p), null, "Citation copied");
    });
  },
  show(p) {
    if (!this.el) return;
    this.target = p;
    const box = $("#text").getBoundingClientRect();
    const rect = p.getBoundingClientRect();
    this.el.style.top = `${rect.top - box.top}px`;
    this.el.style.left = `${rect.left - box.left}px`;
    this.el.classList.add("on");
  },
  hide() {
    this.target = null;
    this.el?.classList.remove("on");
  },
};

let anchorFrom = null; // The marker clicked last, where a shift-click range starts.
document.addEventListener("click", (event) => {
  const marker = event.target.closest("a.mk");
  if (!marker) return;
  const id = marker.getAttribute("href").slice(1);
  if (event.shiftKey && anchorFrom) {
    const start = parseAnchor(anchorFrom);
    const end = parseAnchor(id);
    if (start && end && start.section === end.section) {
      event.preventDefault();
      const hash = rangeHash(start.section, start.markers, end.markers);
      history.replaceState(null, "", `#${hash}`);
      highlightRange(parseRange(hash), false);
      copy(citation(start.section, start.markers, end.markers), null, `${citation(start.section, start.markers, end.markers)} — copied`);
      return;
    }
  }
  anchorFrom = id;
  for (const el of $$(".p.hl")) el.classList.remove("hl");
});

// Selecting text: copy it with its pinpoint citation.

const Quote = {
  pill: null,
  build() {
    const pill = document.createElement("div");
    pill.className = "quote-pill";
    pill.hidden = true;
    pill.innerHTML = `<button type="button" data-q="quote">Copy with citation</button><button type="button" data-q="cite">Cite</button>`;
    document.body.append(pill);
    this.pill = pill;
    pill.addEventListener("pointerdown", (event) => event.preventDefault());
    pill.addEventListener("click", (event) => {
      const kind = event.target.closest("[data-q]")?.dataset.q;
      if (kind) this.copy(kind === "quote");
    });
    let timer;
    document.addEventListener("selectionchange", () => {
      clearTimeout(timer);
      timer = setTimeout(() => this.update(), 140);
    });
  },
  update() {
    const selection = getSelection();
    const text = $("#text");
    if (!text || !selection.rangeCount || selection.isCollapsed) return this.hide();
    const range = selection.getRangeAt(0);
    if (!text.contains(range.commonAncestorContainer) || selection.toString().trim().length < 2) return this.hide();
    const rect = range.getBoundingClientRect();
    const pill = this.pill;
    pill.hidden = false;
    const width = pill.offsetWidth;
    pill.style.top = `${rect.top + scrollY - pill.offsetHeight - 10}px`;
    pill.style.left = `${Math.max(8, Math.min(innerWidth - width - 8, rect.left + scrollX + rect.width / 2 - width / 2))}px`;
  },
  hide() {
    if (this.pill) this.pill.hidden = true;
  },
  copy(withQuote) {
    const selection = getSelection();
    if (!selection.rangeCount) return;
    const range = selection.getRangeAt(0);
    const node = (n) => (n.nodeType === 1 ? n : n.parentElement);
    const first = node(range.startContainer).closest(".p");
    const last = node(range.endContainer).closest(".p");
    const a = parseAnchor(first?.id);
    const b = parseAnchor(last?.id);
    let cite;
    let anchor = "";
    if (a && b && a.section === b.section && first !== last) {
      cite = citation(a.section, a.markers, b.markers);
      anchor = rangeHash(a.section, a.markers, b.markers);
    } else if (a) {
      cite = citation(a.section, a.markers);
      anchor = first.id;
    } else {
      cite = sectionCite(node(range.startContainer));
    }
    const url = official(anchor);
    const quote = selection.toString().replace(/\s+/g, " ").trim();
    let text;
    let html;
    if (!withQuote) {
      text = cite;
      html = `<a href="${escapeHtml(url)}">${escapeHtml(cite)}</a>`;
    } else if (prefs.cite === "bluebook") {
      text = `“${quote}” ${cite}.`;
      html = `“${escapeHtml(quote)}” <a href="${escapeHtml(url)}">${escapeHtml(cite)}</a>.`;
    } else {
      text = `“${quote}”\n— ${cite} (${asOf()}), ${url}`;
      html = `<blockquote>“${escapeHtml(quote)}”</blockquote><p>— <a href="${escapeHtml(url)}">${escapeHtml(cite)}</a> (${escapeHtml(asOf())})</p>`;
    }
    copy(text, html, withQuote ? "Quote copied with its citation" : "Citation copied");
    this.hide();
  },
};

// Hover cards: where a cross-reference goes, what a term means -------------------------

const Card = {
  el: $("#card"),
  owner: null,
  showTimer: 0,
  hideTimer: 0,
  previews: new Map(),
  anchored: CSS.supports("position-area: bottom") && CSS.supports("anchor-name: --a"),
  schedule(owner) {
    clearTimeout(this.hideTimer);
    if (owner === this.owner) return;
    clearTimeout(this.showTimer);
    this.showTimer = setTimeout(() => this.open(owner), owner.matches(".term") ? 380 : 300);
  },
  leave() {
    clearTimeout(this.showTimer);
    clearTimeout(this.hideTimer);
    this.hideTimer = setTimeout(() => this.hide(), 260);
  },
  async open(owner) {
    if (!this.el) return;
    this.place(owner);
    this.el.classList.add("loading");
    this.el.innerHTML = `<div class="body"></div>`;
    this.show();
    let html;
    try {
      html = owner.matches(".term") ? Terms.card(owner) : await this.preview(owner);
    } catch {
      html = `<div class="body"><p>Can’t preview this now.</p></div>`;
    }
    if (this.owner !== owner) return;
    this.el.classList.remove("loading");
    this.el.innerHTML = html;
  },
  async preview(link) {
    const href = link.getAttribute("href");
    const [path, fragment] = href.split("#");
    if (path === location.pathname && fragment && document.getElementById(decodeURIComponent(fragment))) {
      const target = paragraphFor(decodeURIComponent(fragment));
      const parts = paragraphsBetween(target, target).slice(0, 6);
      const found = parseAnchor(target.id);
      const cite = found ? citation(found.section, found.markers, null, "ofr") : page.cite;
      return this.html({ citation: cite, heading: "", note: "On this page", href, html: parts.map((p) => p.outerHTML).join("") });
    }
    if (!this.previews.has(href)) this.previews.set(href, json(`/api/preview?href=${encodeURIComponent(href)}`));
    const data = await this.previews.get(href);
    if (data.error) throw new Error(data.error);
    return this.html({ ...data, href });
  },
  html({ citation, heading, href, html, note = "" }) {
    const words = heading.replace(/^§+\s*[\w.-]+\s*/, "");
    const title = words || heading;
    return `<header><a href="${escapeHtml(href)}">${escapeHtml(citation)}</a>${title ? `<span class="h">${escapeHtml(title)}</span>` : ""}</header>
      <div class="body">${html}</div>
      <footer><span class="scope">${escapeHtml(note)}</span><span class="acts"><a href="${escapeHtml(href)}">Open</a><button type="button" data-beside="${escapeHtml(href)}">Open beside</button></span></footer>`;
  },
  place(owner) {
    if (this.owner && this.owner !== owner) {
      this.owner.classList.remove("open");
      this.owner.style.removeProperty("anchor-name");
    }
    this.owner = owner;
    owner.classList.add("open");
    if (this.anchored) {
      owner.style.setProperty("anchor-name", "--hovered");
      this.el.classList.add("anchored");
    }
  },
  show() {
    try {
      this.el.showPopover();
    } catch {}
    if (!this.anchored) this.position();
  },
  position() {
    const rect = this.owner.getBoundingClientRect();
    const width = this.el.offsetWidth;
    const height = this.el.offsetHeight;
    let top = rect.bottom + 8;
    if (top + height > innerHeight - 8 && rect.top - height - 8 > 8) top = rect.top - height - 8;
    this.el.style.top = `${top}px`;
    this.el.style.left = `${Math.max(8, Math.min(innerWidth - width - 8, rect.left))}px`;
  },
  hide(now = false) {
    if (!this.owner && !now) return;
    this.owner?.classList.remove("open");
    this.owner?.style.removeProperty("anchor-name");
    this.owner = null;
    try {
      this.el?.hidePopover();
    } catch {}
  },
};

const owners = "a.xref, .term";
document.addEventListener("pointerover", (event) => {
  if (event.pointerType === "touch") return;
  const owner = event.target.closest(owners);
  if (owner) Card.schedule(owner);
  else if (event.target.closest("#card")) clearTimeout(Card.hideTimer);
});
document.addEventListener("pointerout", (event) => {
  const from = event.target.closest(`${owners}, #card`);
  const to = event.relatedTarget?.closest?.(`${owners}, #card`);
  if (from && from !== to) Card.leave();
});
document.addEventListener("focusin", (event) => {
  const owner = event.target.closest(owners);
  if (owner) Card.schedule(owner);
});
document.addEventListener("focusout", (event) => {
  if (event.target.closest(owners) && !event.relatedTarget?.closest?.("#card")) Card.leave();
});
document.addEventListener("click", (event) => {
  const term = event.target.closest(".term");
  if (term) Card.open(term);
});

// Defined terms: underlined where used, defined on hover ------------------------------------

const Terms = {
  byKey: new Map(),
  async mark() {
    const text = $("#text");
    if (!text || !page.address || body.dataset.view !== "read") return;
    const query = new URLSearchParams({ address: page.address });
    if (!page.current) query.set("date", page.date);
    let data;
    try {
      data = await json(`/api/terms?${query}`);
    } catch {
      return;
    }
    const terms = data.terms || [];
    if (!terms.length) return;
    const homes = new Set(terms.map((t) => t.address));
    const patterns = [];
    for (const term of terms.sort((a, b) => b.term.length - a.term.length)) {
      this.byKey.set(term.term.toLowerCase(), term);
      let pattern = term.term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
      pattern = /y$/i.test(term.term) ? pattern.slice(0, -1) + "(?:y|ies)" : pattern + "(?:s|es)?";
      patterns.push(pattern);
    }
    const finder = new RegExp(`(?<![\\w-])(?:${patterns.join("|")})(?![\\w-])`, "gi");
    const blocks = $$("#text p").filter((p) => !p.closest(".note, .contents-inline, .meta") && !homes.has(p.closest("section.div[data-address]")?.dataset.address));
    let i = 0;
    const work = (deadline) => {
      while (i < blocks.length && (!deadline || deadline.timeRemaining() > 2)) this.markIn(blocks[i++], finder);
      if (i < blocks.length) idle(work);
    };
    idle(work);
  },
  keyFor(text) {
    const lower = text.toLowerCase().replace(/\s+/g, " ");
    for (const form of [lower, lower.replace(/ies$/, "y"), lower.replace(/es$/, ""), lower.replace(/s$/, "")]) {
      if (this.byKey.has(form)) return form;
    }
    return null;
  },
  markIn(block, finder) {
    const seen = new Set();
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
      acceptNode: (node) => {
        const parent = node.parentElement;
        if (parent.closest("a, .mk, .term, code, sup, sub")) return NodeFilter.FILTER_REJECT;
        // A paragraph's run-in heading ("(1) *Covered entities: ...*") isn't prose.
        const em = parent.closest("em");
        if (em && em.previousElementSibling?.classList.contains("mk")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    for (let node of nodes) {
      finder.lastIndex = 0;
      let found;
      while ((found = finder.exec(node.data))) {
        const key = this.keyFor(found[0]);
        if (!key || seen.has(key)) continue;
        const term = this.byKey.get(key);
        const short = term.term.length <= 5 && term.term === term.term.toUpperCase();
        if (short && found[0] !== term.term && found[0] !== `${term.term}s`) continue;
        seen.add(key);
        const word = node.splitText(found.index);
        node = word.splitText(found[0].length);
        const span = document.createElement("span");
        span.className = "term";
        span.tabIndex = 0;
        span.dataset.term = key;
        span.setAttribute("aria-description", `Defined in ${term.citation}`);
        word.replaceWith(span);
        span.append(word);
        finder.lastIndex = 0;
      }
    }
  },
  card(el) {
    const term = this.byKey.get(el.dataset.term);
    const href = `${term.href}#:~:text=${encodeURIComponent(term.term)}`;
    return `<header><a href="${escapeHtml(href)}">Defined in ${escapeHtml(term.citation)}</a><span class="h">${escapeHtml(term.term)}</span></header>
      <div class="body">${term.html}</div>
      <footer><span class="scope">For this ${escapeHtml(term.scope)}</span><span class="acts"><a href="${escapeHtml(href)}">Open</a><button type="button" data-beside="${escapeHtml(href)}">Open beside</button></span></footer>`;
  },
};

// History: when this text last changed, and marks on sections changed lately --------------------

let historyData = null;
function changesData() {
  const source = $("cfr-timeline")?.dataset.history;
  if (!source) return Promise.resolve(null);
  historyData ||= json(source).catch(() => null);
  return historyData;
}

async function annotateChanges() {
  if (body.dataset.view !== "read") return;
  const data = await changesData();
  if (!data || !data.changes?.length) return;
  const inForce = data.changes.filter((c) => c.effective <= page.date);
  const latest = inForce.reduce((a, c) => (!a || c.effective > a.effective ? c : a), null);
  const amended = $("[data-amended]");
  if (latest && amended) {
    amended.hidden = false;
    amended.innerHTML = `<span class="sep" aria-hidden="true">·</span> <a href="${escapeHtml(latest.compare)}">Amended ${longDate(latest.effective)}</a>`;
  }
  const horizon = new Date(page.date);
  horizon.setDate(horizon.getDate() - 400);
  const recent = horizon.toISOString().slice(0, 10);
  for (const section of $$("section.div.section[data-address], section.div.appendix[data-address]")) {
    const id = section.dataset.address.split("/").pop();
    const change = inForce.find((c) => c.identifier === id || c.address === section.dataset.address);
    if (!change || change.effective < recent) continue;
    const heading = $(":scope > h1, :scope > h2, :scope > h3, :scope > h4", section);
    if (!heading || $(".changed-tick", heading)) continue;
    const tick = Object.assign(document.createElement("a"), { className: "changed-tick", href: change.compare });
    tick.title = `Amended ${longDate(change.effective)} — see what changed`;
    tick.setAttribute("aria-label", tick.title);
    heading.append(tick);
  }
}

// The date: a line of versions to read or compare ---------------------------------------------

class Timeline extends HTMLElement {
  connectedCallback() {
    this.loaded = false;
    this.closest("[popover]")?.addEventListener("toggle", (event) => {
      if (event.newState === "open") this.load();
    });
  }
  async load() {
    if (this.loaded) return;
    this.loaded = true;
    const data = await changesData();
    const dates = [...new Set((data?.dates || []).filter(Boolean))].sort();
    const box = document.createElement("div");
    box.className = "versions";
    if (!dates.length) {
      box.innerHTML = `<p class="versions-summary">No recorded changes.</p>`;
      this.prepend(box);
      return;
    }
    const now = this.dataset.date;
    const first = dates[0];
    const last = dates[dates.length - 1] > now ? dates[dates.length - 1] : now;
    const span = Math.max(1, Date.parse(last) - Date.parse(first));
    const inForce = dates.filter((d) => d <= now).pop();
    box.innerHTML = `<p class="versions-summary"><strong>${dates.length}</strong> version${dates.length === 1 ? "" : "s"} since ${longDate(first)}</p>
      <div class="track" role="group" aria-label="Versions by date">
        ${dates
          .map((d) => `<button type="button" class="tick" style="left:${((Date.parse(d) - Date.parse(first)) / span) * 100}%" data-date="${d}" aria-label="${longDate(d)}" title="${longDate(d)}"${d === inForce ? ' aria-current="true"' : ""}></button>`)
          .join("")}
        <div class="ends"><span>${first.slice(0, 4)}</span><span>${last.slice(0, 4)}</span></div>
      </div>
      <div class="choice" hidden></div>`;
    this.prepend(box);
    const choice = $(".choice", box);
    box.addEventListener("click", (event) => {
      const tick = event.target.closest(".tick");
      if (!tick) return;
      for (const t of $$(".tick", box)) t.classList.toggle("picked", t === tick);
      const date = tick.dataset.date;
      const previous = new Date(date);
      previous.setDate(previous.getDate() - 1);
      const before = previous.toISOString().slice(0, 10);
      const read = date >= dates[dates.length - 1] ? `/current/${page.rest}` : `/on/${date}/${page.rest}`;
      const [later, earlier] = date > now ? [date, now] : [now, date];
      choice.hidden = false;
      choice.innerHTML = `<a class="button primary" href="${read}">Read as of ${longDate(date)}</a>
        <a class="button" href="/compare/${date}/to/${before}/${page.rest}">What changed</a>
        ${date !== now && date !== inForce ? `<a class="button" href="/compare/${later}/to/${earlier}/${page.rest}">Compare with ${longDate(now)}</a>` : ""}`;
    });
  }
}
customElements.define("cfr-timeline", Timeline);

// The outline rail ---------------------------------------------------------------------------

const Rail = {
  el: null,
  build() {
    if (this.el || !$("#text")) return;
    const sections = $$("#text section.div:not(.part):not(.title) > :is(h2, h3, h4)");
    let items;
    if (sections.length > 1) {
      items = sections.map((h) => ({ target: h.parentElement, label: h.innerText.replace(/#\s*$/, "").trim(), level: 1 }));
    } else {
      items = $$("#text .p.d1, #text .p.d2")
        .filter((p) => p.classList.contains("d1") || p.querySelector(":scope > em"))
        .map((p) => {
          const heading = p.querySelector(":scope > em")?.innerText || p.innerText.slice((p.querySelector(".mk")?.innerText || "").length, 70);
          return { target: p, label: `${p.querySelector(".mk")?.innerText || ""} ${heading}`.trim(), level: depthOf(p) };
        });
    }
    if (items.length < 2) return;
    const nav = document.createElement("nav");
    nav.className = "rail";
    nav.setAttribute("aria-label", "Outline");
    nav.innerHTML = `<ol>${items.map((it, n) => `<li><a href="#${escapeHtml(it.target.id)}" class="l${it.level}" data-n="${n}">${escapeHtml(it.label)}</a></li>`).join("")}</ol>`;
    document.body.append(nav);
    this.el = nav;
    const links = $$("a", nav);
    const seen = new Map();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) seen.set(entry.target, entry.isIntersecting);
        const first = items.findIndex((it) => seen.get(it.target));
        if (first < 0) return;
        links.forEach((a, n) => a.classList.toggle("on", n === first));
        links[first].scrollIntoView({ block: "nearest" });
      },
      { rootMargin: "-10% 0px -55% 0px" }
    );
    for (const it of items) observer.observe(it.target);
  },
};

// Side by side ---------------------------------------------------------------------------------

const Split = {
  open(href) {
    let aside = $(".beside");
    if (!aside) {
      aside = document.createElement("aside");
      aside.className = "beside";
      aside.setAttribute("aria-label", "Side by side");
      aside.innerHTML = `<header><a class="here" href="#">Open here</a><button type="button" aria-label="Close side by side (\\)" title="Close (\\)">×</button></header><iframe title="Second provision"></iframe>`;
      document.body.append(aside);
      $("button", aside).addEventListener("click", () => this.close());
    }
    const url = new URL(href, location.href);
    $("a.here", aside).href = url.pathname + url.hash;
    url.searchParams.set("embed", "1");
    $("iframe", aside).src = url.pathname + url.search + url.hash;
    root.classList.add("split");
    const here = new URL(location.href);
    here.searchParams.set("beside", href);
    history.replaceState(null, "", here);
  },
  close() {
    root.classList.remove("split");
    const here = new URL(location.href);
    here.searchParams.delete("beside");
    history.replaceState(null, "", here);
  },
};

// The command palette: one box for citations, headings, words, dates and actions ----------------

const Palette = {
  dialog: $("#palette"),
  input: $("#palette input"),
  list: $("#palette-list"),
  entries: [],
  aliases: [],
  loaded: null,
  items: [],
  selected: 0,
  generation: 0,
  open(text = "") {
    if (!this.dialog || this.dialog.open) return;
    Card.hide(true);
    this.dialog.showModal();
    this.input.value = text;
    this.input.setSelectionRange(text.length, text.length);
    this.load();
    this.update();
  },
  close() {
    this.dialog?.close();
  },
  load() {
    this.loaded ||= (async () => {
      const [aliases, cached] = await Promise.all([json("/static/aliases.json").catch(() => []), json("/api/index").catch(() => ({ titles: [] }))]);
      this.aliases = aliases.map(([name, address, words]) => ({ name, address, words: `${name} ${words}`.toLowerCase() }));
      const titles = [...new Set([page.title, ...(cached.titles || []).map(String)].filter(Boolean))];
      const fetchTitle = async (t) => {
        const data = await json(`/api/index/${t}`).catch(() => ({ entries: [] }));
        for (const e of data.entries) e.k = `${e.c} ${e.h}`.toLowerCase();
        this.entries.push(...data.entries);
        if (this.dialog.open) this.update();
      };
      if (titles[0]) await fetchTitle(titles[0]);
      for (const t of titles.slice(1)) idle(() => fetchTitle(t));
    })();
  },
  update() {
    const generation = ++this.generation;
    const query = this.input.value.trim();
    const groups = this.local(query);
    this.render(groups, query);
    if (!query) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.remote(query, generation, groups), 180);
  },
  local(query) {
    const q = query.toLowerCase();
    const words = q.split(/\s+/).filter(Boolean);
    const groups = [];
    if (!q) {
      groups.push({ name: "Recent", items: recent().filter((r) => r.href !== location.pathname).slice(0, 6).map((r) => ({ c: r.cite, t: r.heading, href: r.href })) });
      groups.push({ name: "Actions", items: actions("").slice(0, 6) });
      return groups.filter((g) => g.items.length);
    }
    const { cite, date } = splitDate(query);
    if (date && page.address) {
      const label = date === "current" ? "the current text" : longDate(date);
      const target = date === "current" ? `/current/${page.rest}` : `/go?${new URLSearchParams({ cite: page.address, date })}`;
      if (!cite) groups.push({ name: "Date", items: [{ c: `As of ${label}`, t: page.cite, href: target }] });
    }
    const aliases = this.aliases.filter((a) => words.every((w) => a.words.includes(w))).slice(0, 4);
    if (aliases.length) groups.push({ name: "Known as", items: aliases.map((a) => ({ c: a.name, t: a.address, href: `/go?cite=${encodeURIComponent(a.address)}` })) });
    const found = [];
    if (words.length) {
      const number = /\d/.test(q) ? q.replace(/^(?:§+|sec(?:tion)?\.?)\s*/, "") : null;
      for (const e of this.entries) {
        let score = 0;
        if (number) {
          const c = e.c.toLowerCase();
          if (c.endsWith(` ${number}`) || c === number) score = 100;
          else if (c.includes(` ${number}`)) score = 70 - c.length / 100;
        }
        if (!score && words.every((w) => e.k.includes(w))) score = 40 - e.k.indexOf(words[0]) / 50 + (e.l === "section" ? 2 : 0);
        if (score > 0) found.push([score, e]);
      }
      found.sort((a, b) => b[0] - a[0]);
    }
    if (found.length) groups.push({ name: "Headings", items: found.slice(0, 8).map(([, e]) => ({ c: e.c, t: e.h.replace(/^§+\s*[\w.-]+\s*/, ""), href: e.u, words })) });
    const acts = actions(q);
    if (acts.length) groups.push({ name: "Actions", items: acts.slice(0, 5) });
    return groups;
  },
  async remote(query, generation, groups) {
    const { cite, date } = splitDate(query);
    const asked = cite || query;
    const tries = [];
    if (/\d/.test(asked)) {
      tries.push(asked);
      if (page.title && !/c\.?\s*f\.?\s*r|^\d+\s*\//i.test(asked) && /^(?:§+\s*)?\d+[a-z]?\.\d|^(?:part|subpart|appendix|§)/i.test(asked)) tries.unshift(`${page.title} CFR ${asked}`);
    }
    const go = (async () => {
      for (const text of tries) {
        const params = new URLSearchParams({ cite: text });
        if (date && date !== "current") params.set("date", date);
        try {
          const found = await json(`/go.json?${params}`);
          return { c: `Go to ${found.citation}`, t: date && date !== "current" ? `as of ${longDate(date)}` : "", href: found.href, first: true };
        } catch {}
      }
      return null;
    })();
    const words = asked.replace(/[^\w\s"'-]/g, " ").trim();
    const within = page.title ? `${page.title} CFR` : "";
    const search = words.length > 2 ? json(`/search.json?${new URLSearchParams({ q: words, limit: "6", ...(within ? { within } : {}) })}`).catch(() => null) : Promise.resolve(null);
    const place = await go;
    if (generation !== this.generation) return;
    if (place) {
      groups = [{ name: "Citation", items: [place] }, ...groups.filter((g) => g.name !== "Date" || !date)];
      this.render(groups, query, true);
    } else {
      this.render(groups, query, true);
    }
    const results = await search;
    if (generation !== this.generation) return;
    const items = (results?.results || []).map((r) => ({ c: r.citation, t: r.heading, ex: r.excerpt, href: r.href, words: words.toLowerCase().split(/\s+/) }));
    items.push({ c: `Search ${within || "all titles"} for “${words}”`, t: "", href: `/search?${new URLSearchParams({ q: words, ...(within ? { within } : {}) })}` });
    if (within) items.push({ c: `Search all titles for “${words}”`, t: "", href: `/search?${new URLSearchParams({ q: words })}` });
    this.render([...groups, { name: "In the text", items }], query);
  },
  render(groups, query, pending = false) {
    this.items = groups.flatMap((g) => g.items);
    if (this.selected >= this.items.length) this.selected = 0;
    let n = 0;
    const mark = (text, words) => {
      let html = escapeHtml(text);
      for (const w of words || []) if (w.length > 1) html = html.replace(new RegExp(`(${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi"), "<mark>$1</mark>");
      return html;
    };
    const html = groups
      .filter((g) => g.items.length)
      .map(
        (g) =>
          `<li class="group" role="presentation">${escapeHtml(g.name)}</li>` +
          g.items
            .map((it) => {
              const i = n++;
              return `<li role="option" id="opt-${i}" data-i="${i}" aria-selected="${i === this.selected}"><span class="c">${escapeHtml(it.c)}</span><span class="t">${mark(it.t || "", it.words)}</span><span class="x">${it.x || ""}</span>${it.ex ? `<span class="ex">${mark(it.ex, it.words)}</span>` : ""}</li>`;
            })
            .join("")
      )
      .join("");
    this.list.innerHTML = html || (query ? `<li class="empty">${pending ? "Looking…" : "Nothing yet — press Enter to go to it or search for it"}</li>` : "");
    if (pending && html) this.list.insertAdjacentHTML("beforeend", `<li class="pending" role="presentation">Searching the text…</li>`);
    this.input.setAttribute("aria-activedescendant", this.items.length ? `opt-${this.selected}` : "");
  },
  move(delta) {
    if (!this.items.length) return;
    this.selected = (this.selected + delta + this.items.length) % this.items.length;
    for (const el of $$("[role=option]", this.list)) el.setAttribute("aria-selected", String(Number(el.dataset.i) === this.selected));
    const el = $(`#opt-${this.selected}`, this.list);
    el?.scrollIntoView({ block: "nearest" });
    this.input.setAttribute("aria-activedescendant", el ? el.id : "");
  },
  choose(item, beside = false) {
    if (!item) {
      const form = $("form", this.dialog);
      return form.requestSubmit();
    }
    this.close();
    if (item.run) return item.run();
    if (beside) return Split.open(item.href);
    location.href = item.href;
  },
};

function splitDate(query) {
  const tail = /(?:^|\s+)(?:@|as\s+of\s+|on\s+|in\s+|since\s+)?((?:\d{4}(?:-\d{1,2}(?:-\d{1,2})?)?)|(?:[a-z]{3,9}\.?\s+(?:\d{1,2},?\s+)?\d{4})|today|current)\s*$/i;
  const found = tail.exec(query);
  if (!found) return { cite: query, date: null };
  const rest = query.slice(0, found.index).trim();
  const text = found[1].toLowerCase();
  let date = null;
  if (text === "today" || text === "current") date = "current";
  else if (/^\d{4}/.test(text)) {
    const [y, m = "1", d = "1"] = text.split("-");
    if (Number(y) < 1990 && rest === "") return { cite: query, date: null }; // A part number, not a year.
    date = `${y}-${m.padStart(2, "0")}-${d.padStart(2, "0")}`;
  } else {
    const parsed = new Date(text.replace(/\./, ""));
    if (!Number.isNaN(parsed.getTime())) date = `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, "0")}-${String(parsed.getDate()).padStart(2, "0")}`;
  }
  if (!date) return { cite: query, date: null };
  // "164.502 2019" is ambiguous; only a word or @ makes a trailing year a date.
  if (rest && !/(?:@|as\s+of|on|in|since)\s*$/i.test(query.slice(0, found.index + found[0].length - found[1].length).trim()) && /^\d{4}$/.test(found[1])) return { cite: query, date: null };
  return { cite: rest, date };
}

function actions(q) {
  const all = [];
  const exportLinks = $$(".formats a[download]");
  for (const link of exportLinks) all.push({ c: `Export as ${link.textContent}`, t: page.cite, k: `export download save ${link.textContent} pdf word docx epub html markdown text json`, run: () => link.click() });
  if (page.address) {
    all.push({ c: "History", t: `Changes to ${page.cite}`, k: "history changes versions timeline amended", href: `/history/${page.rest}` });
    all.push({ c: "Compare with an earlier date", t: "Versions and redline", k: "compare redline diff versions date as of", run: () => $("#timeline")?.showPopover() });
    all.push({ c: "Copy citation", t: paragraphCitation($(".p.current")) || page.cite, k: "copy cite citation bluebook", run: () => copy(paragraphCitation($(".p.current")), null, "Citation copied") });
  }
  all.push({ c: "Outline", t: "Show the outline beside the text", k: "outline rail contents toc", run: () => toggle("rail") });
  all.push({ c: "Focus", t: "Hide everything but the text", k: "focus zen distraction", run: () => toggle("focus") });
  all.push({ c: "Dark theme", t: "", k: "dark theme night", run: () => setPref("theme", "dark") });
  all.push({ c: "Light theme", t: "", k: "light theme day", run: () => setPref("theme", "light") });
  all.push({ c: "Theme from the system", t: "", k: "auto theme system", run: () => setPref("theme", "auto") });
  all.push({ c: "Larger text", t: "", k: "larger bigger text size zoom", run: () => step(1) });
  all.push({ c: "Smaller text", t: "", k: "smaller text size zoom", run: () => step(-1) });
  all.push({ c: "Print", t: "A clean, cited copy", k: "print paper pdf", run: () => print() });
  all.push({ c: "Settings", t: "Hosts, cache, limits", k: "settings preferences config hosts cache", href: "/settings" });
  all.push({ c: "Keyboard shortcuts", t: "", k: "keyboard shortcuts keys help", run: () => $("#keys")?.showModal() });
  if (!q) return all.filter((a) => a.href || /History|Compare|Outline|Copy/.test(a.c));
  const words = q.split(/\s+/);
  return all.filter((a) => words.every((w) => `${a.c} ${a.k}`.toLowerCase().includes(w)));
}

function recent() {
  try {
    return JSON.parse(localStorage.getItem("ecfr-recent") || "[]");
  } catch {
    return [];
  }
}

function remember() {
  if (!page.address || body.dataset.embed !== undefined) return;
  const entry = { href: location.pathname, cite: page.cite, heading: page.heading };
  const list = [entry, ...recent().filter((r) => r.href !== entry.href)].slice(0, 12);
  try {
    localStorage.setItem("ecfr-recent", JSON.stringify(list));
  } catch {}
}

if (Palette.dialog) {
  Palette.input.addEventListener("input", () => {
    Palette.selected = 0;
    Palette.update();
  });
  Palette.input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      Palette.move(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      Palette.move(-1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      Palette.choose(Palette.items[Palette.selected], event.shiftKey);
    }
  });
  Palette.list.addEventListener("click", (event) => {
    const option = event.target.closest("[role=option]");
    if (option) Palette.choose(Palette.items[Number(option.dataset.i)], event.shiftKey);
  });
  Palette.dialog.addEventListener("click", (event) => {
    if (event.target === Palette.dialog) Palette.close();
  });
}

for (const input of $$("#find, [data-opens-palette]")) {
  input.addEventListener("focus", () => {
    Palette.open(input.value);
    input.blur();
  });
}
$(".find")?.addEventListener("click", () => Palette.open());

// The keyboard ---------------------------------------------------------------------------------

function currentParagraph() {
  return $(".p.current");
}

function stepParagraph(delta) {
  const all = $$("#text .p");
  if (!all.length) return;
  let at = all.indexOf(currentParagraph());
  if (at < 0) {
    const top = innerHeight * 0.2;
    at = all.findIndex((p) => p.getBoundingClientRect().bottom > top);
    at = delta > 0 ? at - 1 : at + 1;
  }
  const next = all[Math.min(all.length - 1, Math.max(0, at + delta))];
  currentParagraph()?.classList.remove("current");
  next.classList.add("current");
  scroll(next);
  history.replaceState(null, "", `#${next.id}`);
}

const follow = (rel) => {
  const link = $(`link[rel="${rel}"]`);
  if (link) location.href = link.href;
};

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    return Palette.dialog?.open ? Palette.close() : Palette.open();
  }
  if (event.key === "Escape") {
    Card.hide();
    Quote.hide();
    return;
  }
  if (typing(event) || event.metaKey || event.ctrlKey || event.altKey || $("dialog[open]")) return;
  const keys = {
    "/": () => Palette.open(),
    j: () => stepParagraph(1),
    k: () => stepParagraph(-1),
    "]": () => follow("next"),
    "[": () => follow("prev"),
    u: () => follow("up"),
    y: () => copy(paragraphCitation(currentParagraph()), null, `${paragraphCitation(currentParagraph())} — copied`),
    o: () => toggle("rail"),
    f: () => toggle("focus"),
    d: () => $("#timeline")?.togglePopover(),
    "\\": () => Split.close(),
    "?": () => $("#keys")?.showModal(),
  };
  const action = keys[event.key];
  if (action) {
    event.preventDefault();
    action();
  }
});

// Contents: open a division in place --------------------------------------------------------------

document.addEventListener(
  "toggle",
  async (event) => {
    const details = event.target;
    if (!(details instanceof HTMLDetailsElement) || !details.open || !details.dataset.src) return;
    const kids = $(":scope > .kids", details);
    if (!kids || kids.dataset.loaded) return;
    kids.dataset.loaded = "1";
    try {
      const response = await fetch(details.dataset.src);
      kids.innerHTML = await response.text();
    } catch {
      kids.innerHTML = `<p class="aside">Can’t load this now.</p>`;
    }
  },
  true
);

// Saving exports to the server's folder, without leaving the page ---------------------------------

document.addEventListener("submit", async (event) => {
  const form = event.target.closest("form.save");
  if (!form) return;
  event.preventDefault();
  toast("Saving…");
  try {
    const response = await fetch(form.action, { method: "POST", body: new URLSearchParams(new FormData(form)), headers: { Accept: "application/json" } });
    const data = await response.json();
    toast(response.ok ? `Saved ${data.saved}` : data.error || "Couldn’t save");
  } catch {
    toast("Couldn’t save");
  }
});

// Redline: show only what changed --------------------------------------------------------------------

$("[data-only-changes]")?.addEventListener("change", (event) => {
  $(".redline-body")?.classList.toggle("only", event.target.checked);
});

// Side-by-side pages keep to their pane --------------------------------------------------------------

if (body.dataset.embed !== undefined) {
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href^='/']");
    if (!link || link.hasAttribute("download") || event.defaultPrevented) return;
    const url = new URL(link.href);
    url.searchParams.set("embed", "1");
    link.href = url.pathname + url.search + url.hash;
  });
}

// Start ----------------------------------------------------------------------------------------------

applyPrefs();
Tools.build();
Quote.build();
readHash();
remember();
const beside = new URLSearchParams(location.search).get("beside");
if (beside && beside.startsWith("/")) Split.open(beside);
whenShown(() => {
  Terms.mark();
  idle(annotateChanges);
});
if ("serviceWorker" in navigator && body.dataset.embed === undefined) {
  navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
}
