// Unit tests for webapp/static/search.js
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const searchJsCode = fs.readFileSync(path.join(__dirname, "static", "search.js"), "utf-8");

function createMockDom() {
  class MockElement {
    constructor(tagName) {
      this.tagName = tagName.toUpperCase();
      this.classList = {
        _classes: new Set(),
        add: (...cls) => cls.forEach((c) => this.classList._classes.add(c)),
        remove: (...cls) => cls.forEach((c) => this.classList._classes.delete(c)),
        contains: (c) => this.classList._classes.has(c),
      };
      this.attributes = {};
      this.children = [];
      this.parentElement = null;
      this._value = "";
      this._textContent = "";
      this._hidden = false;
      this._handlers = new Map();
    }

    get hidden() {
      return this._hidden;
    }

    set hidden(v) {
      this._hidden = Boolean(v);
    }

    get value() {
      return this._value;
    }

    set value(v) {
      this._value = v;
    }

    get textContent() {
      return this._textContent;
    }

    set textContent(v) {
      this._textContent = String(v);
    }

    get innerHTML() {
      return "";
    }

    set innerHTML(v) {
      if (v === "") {
        this.children = [];
      }
    }

    get href() {
      return this.attributes.href || null;
    }

    set href(v) {
      this.attributes.href = String(v);
    }

    get className() {
      return Array.from(this.classList._classes).join(" ");
    }

    set className(v) {
      this.classList._classes = new Set(v.split(/\s+/).filter(Boolean));
    }

    setAttribute(name, value) {
      this.attributes[name] = String(value);
    }

    getAttribute(name) {
      return this.attributes[name] || null;
    }

    removeAttribute(name) {
      delete this.attributes[name];
    }

    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      return child;
    }

    contains(node) {
      if (node === this) return true;
      for (const child of this.children) {
        if (child.contains && child.contains(node)) return true;
      }
      return false;
    }

    scrollIntoView() {}
    focus() {}

    addEventListener(event, fn) {
      if (!this._handlers.has(event)) {
        this._handlers.set(event, []);
      }
      this._handlers.get(event).push(fn);
    }

    dispatchEvent(event) {
      const handlers = this._handlers.get(event.type) || [];
      handlers.forEach((h) => h(event));
    }

    click() {
      this.dispatchEvent({ type: "click", target: this });
    }

    querySelector(sel) {
      const results = this.querySelectorAll(sel);
      return results.length > 0 ? results[0] : null;
    }

    querySelectorAll(sel) {
      const results = [];
      function matches(child) {
        if (sel.startsWith(".")) return child.classList.contains(sel.slice(1));
        if (sel.startsWith("#")) return child.id === sel.slice(1);
        if (child.tagName && child.tagName.toLowerCase() === sel.toLowerCase()) return true;
        return false;
      }
      function walk(node) {
        for (const child of node.children) {
          if (matches(child)) results.push(child);
          walk(child);
        }
      }
      walk(this);
      return results;
    }
  }

  const documentListeners = new Map();

  const mockDocument = {
    readyState: "complete",
    createElement: (tag) => new MockElement(tag),
    addEventListener: (event, fn) => {
      if (!documentListeners.has(event)) documentListeners.set(event, []);
      documentListeners.get(event).push(fn);
    },
    querySelector: (sel) => {
      return mockDocument.body ? mockDocument.body.querySelector(sel) : null;
    },
    body: new MockElement("body"),
  };

  const mockWindow = {
    document: mockDocument,
    addEventListener: () => {},
    fetch: () => Promise.reject(new Error("mock fetch not configured")),
  };

  return { mockWindow, mockDocument };
}

function setupSearchEnvironment(fetchData = []) {
  const { mockWindow, mockDocument } = createMockDom();
  mockWindow.fetch = (url) => {
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(fetchData),
    });
  };

  const context = vm.createContext({
    window: mockWindow,
    document: mockDocument,
    fetch: mockWindow.fetch,
    console,
    Promise,
    Array,
    Object,
    String,
    Boolean,
    Set,
    Map,
    Error,
    setTimeout,
    clearTimeout,
  });

  vm.runInContext(searchJsCode, context);
  return { context, mockWindow, mockDocument };
}

test("search.js: transliteration algorithms match Latin and Cyrillic variants", () => {
  const { mockWindow } = setupSearchEnvironment();
  const ds = mockWindow.DaugavpilsSearch;

  // Exact Cyrillic
  assert.ok(ds.textMatches("Ящер", "ящер"));
  // Latin transliterations of "Ящер"
  assert.ok(ds.textMatches("Ящер", "yashcher"));
  assert.ok(ds.textMatches("Ящер", "iashcher"));
  assert.ok(ds.textMatches("Ящер", "yascher"));

  // Cyrillic to Latin
  assert.ok(ds.textMatches("Двинск", "dvinsk"));
  assert.ok(ds.textMatches("M. Spirit", "спирит"));
  assert.ok(ds.textMatches("M. Spirit", "spirit"));

  // Band members
  assert.ok(ds.textMatches("Владислав Петкун", "petkun"));
  assert.ok(ds.textMatches("Владислав Петкун", "петкун"));
  assert.ok(ds.textMatches("Владислав Петкун", "vladislav"));

  // Ё / е equivalence
  assert.ok(ds.textMatches("Ёлочка", "елочка"));
  assert.ok(ds.textMatches("Ёлочка", "elochka"));
  assert.ok(ds.textMatches("Ёлочка", "iolochka"));
});

test("search.js: searchIndex matches bands, releases, tracks and filters by genre/year/members", () => {
  const { mockWindow } = setupSearchEnvironment();
  const ds = mockWindow.DaugavpilsSearch;

  const mockIndex = [
    {
      type: "band",
      name: "M. Spirit",
      url: "/bands/m-spirit/",
      genres: ["Punk Rock", "Indie"],
      years: "1994-1996",
      members: ["Владислав Петкун", "Сергей Валевко"],
      roles: ["гитара, вокал", "вокал"],
    },
    {
      type: "band",
      name: "Двинск",
      url: "/bands/dvinsk/",
      genres: ["Hard Rock"],
      years: "1988-1994",
      members: ["Кузя"],
      roles: ["гитара"],
    },
    {
      type: "release",
      name: "Задушевные песенки М.С. Панкухина",
      band: "M. Spirit",
      year: "1995",
      url: "/bands/m-spirit/1995-zadushevnie-pesenki-ms-pankukhina/",
    },
    {
      type: "track",
      name: "Ящер",
      band: "M. Spirit",
      release: "Задушевные песенки М.С. Панкухина",
      url: "/bands/m-spirit/1995-zadushevnie-pesenki-ms-pankukhina/#track-1",
    },
  ];

  // 1. Search by track name (both Cyrillic and Latin)
  const trackResCyr = ds.searchIndex(mockIndex, "Ящер");
  assert.equal(trackResCyr.tracks.length, 1);
  assert.equal(trackResCyr.tracks[0].name, "Ящер");

  const trackResLat = ds.searchIndex(mockIndex, "iashcher");
  assert.equal(trackResLat.tracks.length, 1);
  assert.equal(trackResLat.tracks[0].name, "Ящер");

  // 2. Search by musician
  const memberRes = ds.searchIndex(mockIndex, "Петкун");
  assert.equal(memberRes.bands.length, 1);
  assert.equal(memberRes.bands[0].name, "M. Spirit");

  const memberResLat = ds.searchIndex(mockIndex, "petkun");
  assert.equal(memberResLat.bands.length, 1);
  assert.equal(memberResLat.bands[0].name, "M. Spirit");

  // 3. Search by genre
  const genreRes = ds.searchIndex(mockIndex, "punk");
  assert.equal(genreRes.bands.length, 1);
  assert.equal(genreRes.bands[0].name, "M. Spirit");

  // 4. Search by year
  const yearRes = ds.searchIndex(mockIndex, "1995");
  assert.equal(yearRes.releases.length, 1);
  assert.equal(yearRes.releases[0].name, "Задушевные песенки М.С. Панкухина");
});

test("search.js: initSearch DOM interactions, keyboard navigation, and clear button", async () => {
  const mockIndex = [
    {
      type: "band",
      name: "M. Spirit",
      url: "/bands/m-spirit/",
      genres: ["Punk Rock"],
      years: "1994-1996",
      members: ["Владислав Петкун"],
    },
    {
      type: "track",
      name: "Ящер",
      band: "M. Spirit",
      release: "Задушевные песенки",
      url: "/bands/m-spirit/1995-zadushevnie-pesenki/#track-1",
    },
  ];

  const { mockWindow, mockDocument } = setupSearchEnvironment(mockIndex);
  const ds = mockWindow.DaugavpilsSearch;

  // Construct DOM
  const container = mockDocument.createElement("div");
  container.className = "site-search";
  container.setAttribute("data-label-bands", "Группы");
  container.setAttribute("data-label-tracks", "Треки");
  container.setAttribute("data-label-no-results", "Ничего не найдено");

  const box = mockDocument.createElement("div");
  box.className = "search-box";

  const input = mockDocument.createElement("input");
  input.className = "search-input";
  input.setAttribute("data-index-url", "/search-index.json");

  const clearBtn = mockDocument.createElement("button");
  clearBtn.className = "search-clear";
  clearBtn.hidden = true;

  const resultsEl = mockDocument.createElement("div");
  resultsEl.className = "search-results";
  resultsEl.hidden = true;

  box.appendChild(input);
  box.appendChild(clearBtn);
  container.appendChild(box);
  container.appendChild(resultsEl);
  mockDocument.body.appendChild(container);

  ds.initSearch(container);

  // Focus triggers prefetch
  input.dispatchEvent({ type: "focus" });

  // Type query
  input.value = "ящер";
  input.dispatchEvent({ type: "input" });

  // Wait for promise tick
  await new Promise((r) => setTimeout(r, 20));

  assert.equal(resultsEl.hidden, false);
  assert.equal(clearBtn.hidden, false);
  const items = resultsEl.querySelectorAll(".search-result-item");
  assert.equal(items.length, 1);
  assert.equal(items[0].getAttribute("href"), "/bands/m-spirit/1995-zadushevnie-pesenki/#track-1");

  // Keyboard navigation: ArrowDown selects item
  input.dispatchEvent({ type: "keydown", key: "ArrowDown", preventDefault: () => {} });
  assert.ok(items[0].classList.contains("selected"));

  // Pressing Enter clicks item
  let clicked = false;
  items[0].addEventListener("click", () => {
    clicked = true;
  });
  input.dispatchEvent({ type: "keydown", key: "Enter", preventDefault: () => {} });
  assert.ok(clicked);

  // Clear button resets
  clearBtn.dispatchEvent({ type: "click" });
  assert.equal(input.value, "");
  assert.equal(resultsEl.hidden, true);
  assert.equal(clearBtn.hidden, true);
});
