// Unit tests for webapp/static/player.js
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const playerJsCode = fs.readFileSync(path.join(__dirname, "static", "player.js"), "utf-8");

function createMockDom(options = {}) {
  const lang = options.lang || "ru";
  const sessionStorageStore = new Map();
  const localStorageStore = new Map();
  const eventListeners = new Map();

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
      this._textContent = "";
      this.dataset = {};
      this.id = "";
      this.style = {};
      this.disabled = false;
      this.loaded = false;
      this._handlers = new Map();
    }

    get className() {
      return Array.from(this.classList._classes).join(" ");
    }

    set className(v) {
      this.classList._classes = new Set(String(v).split(/\s+/).filter(Boolean));
    }

    get textContent() {
      if (this.children.length === 0) return this._textContent || "";
      return this.children.map((c) => c.textContent).join("");
    }

    set textContent(v) {
      this._textContent = String(v);
      this.children = [];
    }

    addEventListener(evt, fn) {
      if (!this._handlers.has(evt)) this._handlers.set(evt, []);
      this._handlers.get(evt).push(fn);
    }

    click() {
      const handlers = this._handlers.get("click") || [];
      handlers.forEach((fn) => fn({ target: this, preventDefault: () => {} }));
    }

    setAttribute(name, val) {
      this.attributes[name] = val;
    }

    getAttribute(name) {
      return this.attributes[name] || null;
    }

    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      return child;
    }

    insertBefore(newNode, refNode) {
      newNode.parentElement = this;
      const idx = this.children.indexOf(refNode);
      if (idx >= 0) {
        this.children.splice(idx, 0, newNode);
      } else {
        this.children.push(newNode);
      }
      return newNode;
    }

    remove() {
      if (this.parentElement) {
        const idx = this.parentElement.children.indexOf(this);
        if (idx >= 0) this.parentElement.children.splice(idx, 1);
        this.parentElement = null;
      }
    }

    querySelector(sel) {
      return this.querySelectorAll(sel)[0] || null;
    }

    querySelectorAll(sel) {
      const selectors = sel.split(",").map((s) => s.trim());
      const results = [];
      function matchesOne(child) {
        for (const s of selectors) {
          if (s.startsWith(".") && child.classList.contains(s.slice(1))) return true;
          if (s.startsWith("#") && child.id === s.slice(1)) return true;
          if (child.tagName && child.tagName.toLowerCase() === s.toLowerCase()) return true;
        }
        return false;
      }
      function walk(node) {
        for (const child of node.children) {
          if (matchesOne(child)) results.push(child);
          walk(child);
        }
      }
      walk(this);
      return results;
    }

    closest(sel) {
      let cur = this;
      while (cur) {
        if (sel.startsWith(".") && cur.classList && cur.classList.contains(sel.slice(1))) return cur;
        if (cur.tagName && cur.tagName.toLowerCase() === sel.toLowerCase()) return cur;
        cur = cur.parentElement;
      }
      return null;
    }

    load() {
      this.loaded = true;
    }
  }

  const documentElement = new MockElement("html");
  documentElement.lang = lang;
  const body = new MockElement("body");
  documentElement.appendChild(body);

  const mockDocument = {
    documentElement,
    body,
    createElement: (tag) => new MockElement(tag),
    getElementById: (id) => {
      if (body.id === id) return body;
      const all = body.querySelectorAll("#" + id);
      return all[0] || null;
    },
    querySelector: (sel) => body.querySelector(sel),
    querySelectorAll: (sel) => body.querySelectorAll(sel),
    addEventListener: (event, fn, useCapture) => {
      if (!eventListeners.has(event)) eventListeners.set(event, []);
      eventListeners.get(event).push({ fn, useCapture });
    },
    dispatchEvent: (event) => {
      const handlers = eventListeners.get(event.type) || [];
      handlers.forEach((h) => h.fn(event));
    },
  };

  const mockWindow = {
    document: mockDocument,
    sessionStorage: {
      getItem: (k) => (sessionStorageStore.has(k) ? sessionStorageStore.get(k) : null),
      setItem: (k, v) => sessionStorageStore.set(k, String(v)),
      removeItem: (k) => sessionStorageStore.delete(k),
    },
    localStorage: {
      getItem: (k) => (localStorageStore.has(k) ? localStorageStore.get(k) : null),
      setItem: (k, v) => localStorageStore.set(k, String(v)),
      removeItem: (k) => localStorageStore.delete(k),
    },
    addEventListener: () => {},
    navigator: { onLine: true },
    CustomEvent: class CustomEvent {
      constructor(type, init = {}) {
        this.type = type;
        this.detail = init.detail || {};
      }
    },
    fetch: async () => ({ ok: true }),
  };

  return { mockWindow, mockDocument, eventListeners };
}

test("player.js: showBanner renders Russian text by default", () => {
  const { mockWindow, mockDocument } = createMockDom({ lang: "ru" });
  const context = vm.createContext({
    window: mockWindow,
    document: mockDocument,
    sessionStorage: mockWindow.sessionStorage,
    localStorage: mockWindow.localStorage,
    CustomEvent: mockWindow.CustomEvent,
    fetch: mockWindow.fetch,
    setTimeout,
    clearTimeout,
    Date,
    JSON,
  });

  vm.runInContext(playerJsCode, context);
  const DaugavpilsArchiveOutage = context.window.DaugavpilsArchiveOutage;
  assert.ok(DaugavpilsArchiveOutage);

  const banner = DaugavpilsArchiveOutage.showBanner();
  assert.ok(banner);
  assert.equal(banner.id, "archive-outage-banner");
  assert.match(banner.textContent, /Сервера Internet Archive \(archive\.org\) временно недоступны/);
  assert.match(banner.textContent, /Попробовать снова/);
});

test("player.js: showBanner renders English text when lang is en", () => {
  const { mockWindow, mockDocument } = createMockDom({ lang: "en" });
  const context = vm.createContext({
    window: mockWindow,
    document: mockDocument,
    sessionStorage: mockWindow.sessionStorage,
    localStorage: mockWindow.localStorage,
    CustomEvent: mockWindow.CustomEvent,
    fetch: mockWindow.fetch,
    setTimeout,
    clearTimeout,
    Date,
    JSON,
  });

  vm.runInContext(playerJsCode, context);
  const DaugavpilsArchiveOutage = context.window.DaugavpilsArchiveOutage;

  const banner = DaugavpilsArchiveOutage.showBanner();
  assert.ok(banner);
  assert.match(banner.textContent, /Internet Archive \(archive\.org\) servers are temporarily unavailable/);
  assert.match(banner.textContent, /Retry/);
});

test("player.js: handleMediaError marks track row and caches outage", () => {
  const { mockWindow, mockDocument } = createMockDom({ lang: "ru" });
  const context = vm.createContext({
    window: mockWindow,
    document: mockDocument,
    sessionStorage: mockWindow.sessionStorage,
    localStorage: mockWindow.localStorage,
    CustomEvent: mockWindow.CustomEvent,
    fetch: mockWindow.fetch,
    setTimeout,
    clearTimeout,
    Date,
    JSON,
  });

  vm.runInContext(playerJsCode, context);

  // Setup mock tracklist row
  const table = mockDocument.createElement("table");
  table.classList.add("tracklist");
  const tr = mockDocument.createElement("tr");
  const tdPos = mockDocument.createElement("td");
  tdPos.textContent = "1";
  const tdTitle = mockDocument.createElement("td");
  tdTitle.textContent = "Иваново";
  const tdAudio = mockDocument.createElement("td");
  const audio = mockDocument.createElement("audio");
  audio.src = "https://archive.org/download/daugavpils-fans-glazki/05-ivanovo.mp3";
  audio.dataset.track = "Иваново";
  audio.dataset.band = "glazki-stekolshika";
  audio.dataset.release = "chernovyaki";
  audio.error = { code: 4 };

  tdAudio.appendChild(audio);
  tr.appendChild(tdPos);
  tr.appendChild(tdTitle);
  tr.appendChild(tdAudio);
  table.appendChild(tr);
  mockDocument.body.appendChild(table);

  let mediaErrorDispatched = null;
  mockDocument.addEventListener("daugavpils:media-error", (e) => {
    mediaErrorDispatched = e.detail;
  });

  // Trigger error handler
  context.window.DaugavpilsArchiveOutage.handleError(audio);

  // Assert row got media-outage-row class
  assert.ok(tr.classList.contains("media-outage-row"));
  const notice = tr.querySelector(".media-outage-notice");
  assert.ok(notice);
  assert.match(notice.textContent, /сбой archive\.org/);

  // Assert banner is shown
  const banner = mockDocument.getElementById("archive-outage-banner");
  assert.ok(banner);

  // Assert cached in sessionStorage
  assert.ok(context.window.DaugavpilsArchiveOutage.isOutageCached());

  // Assert telemetry custom event dispatched
  assert.ok(mediaErrorDispatched);
  assert.equal(mediaErrorDispatched.track, "Иваново");
  assert.equal(mediaErrorDispatched.band, "glazki-stekolshika");
});

test("player.js: retry clears outage and reloads media when probe succeeds", (t, done) => {
  const { mockWindow, mockDocument } = createMockDom({ lang: "ru" });
  mockWindow.fetch = async () => ({ ok: true });

  const context = vm.createContext({
    window: mockWindow,
    document: mockDocument,
    sessionStorage: mockWindow.sessionStorage,
    localStorage: mockWindow.localStorage,
    CustomEvent: mockWindow.CustomEvent,
    fetch: mockWindow.fetch,
    setTimeout,
    clearTimeout,
    Date,
    JSON,
  });

  vm.runInContext(playerJsCode, context);
  const DaugavpilsArchiveOutage = context.window.DaugavpilsArchiveOutage;
  DaugavpilsArchiveOutage.setOutageCached(true);

  const banner = DaugavpilsArchiveOutage.showBanner();
  const retryBtn = banner.querySelector(".outage-retry-btn");
  assert.ok(retryBtn);

  const audio = mockDocument.createElement("audio");
  mockDocument.body.appendChild(audio);

  DaugavpilsArchiveOutage.retry(retryBtn, banner);

  setTimeout(() => {
    assert.equal(DaugavpilsArchiveOutage.isOutageCached(), false);
    assert.equal(audio.loaded, true);
    assert.equal(mockDocument.getElementById("archive-outage-banner"), null);
    done();
  }, 50);
});

test("player.js: handleMediaError marks image figure and shows outage banner", () => {
  const { mockWindow, mockDocument } = createMockDom({ lang: "ru" });
  const context = vm.createContext({
    window: mockWindow,
    document: mockDocument,
    sessionStorage: mockWindow.sessionStorage,
    localStorage: mockWindow.localStorage,
    CustomEvent: mockWindow.CustomEvent,
    fetch: mockWindow.fetch,
    setTimeout,
    clearTimeout,
    Date,
    JSON,
  });

  vm.runInContext(playerJsCode, context);

  // Setup mock gallery figure
  const fig = mockDocument.createElement("figure");
  const img = mockDocument.createElement("img");
  img.src = "https://archive.org/download/daugavpils-fans-glazki-stekolshchika/photo1.webp";
  img.dataset.band = "glazki-stekolshchika";
  fig.appendChild(img);
  mockDocument.body.appendChild(fig);

  // Trigger error handler for image
  context.window.DaugavpilsArchiveOutage.handleError(img);

  // Assert figure received media-outage-state
  assert.ok(fig.classList.contains("media-outage-state"));
  const notice = fig.querySelector(".media-outage-notice");
  assert.ok(notice);
  assert.match(notice.textContent, /сбой archive\.org/);

  // Assert banner is rendered
  const banner = mockDocument.getElementById("archive-outage-banner");
  assert.ok(banner);
  assert.ok(context.window.DaugavpilsArchiveOutage.isOutageCached());
});
