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
      this.paused = true;
      this.currentTime = 0;
      this.duration = 180;
      this.playbackRate = 1.0;
    }

    play() {
      this.paused = false;
      const handlers = eventListeners.get("play") || [];
      handlers.forEach((h) => h.fn({ target: this, type: "play" }));
      return Promise.resolve();
    }

    pause() {
      this.paused = true;
      const handlers = eventListeners.get("pause") || [];
      handlers.forEach((h) => h.fn({ target: this, type: "pause" }));
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
      if (sel.includes(",")) {
        const parts = sel.split(",").map((s) => s.trim());
        const set = new Set();
        parts.forEach((p) => this.querySelectorAll(p).forEach((el) => set.add(el)));
        return Array.from(set);
      }
      if (sel.includes(" ")) {
        const parts = sel.split(/\s+/).filter(Boolean);
        let current = [this];
        for (const part of parts) {
          const next = [];
          for (const node of current) {
            next.push(...node.querySelectorAll(part));
          }
          current = next;
        }
        return current;
      }
      const results = [];
      function matchesOne(child) {
        if (sel.startsWith(".")) return child.classList.contains(sel.slice(1));
        if (sel.startsWith("#")) return child.id === sel.slice(1);
        if (sel.includes(".")) {
          const [tag, cls] = sel.split(".");
          return (!tag || (child.tagName && child.tagName.toLowerCase() === tag.toLowerCase())) &&
                 child.classList.contains(cls);
        }
        if (child.tagName && child.tagName.toLowerCase() === sel.toLowerCase()) return true;
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

  const mediaSession = {
    metadata: null,
    playbackState: "none",
    positionState: null,
    _actionHandlers: new Map(),
    setActionHandler(action, handler) {
      this._actionHandlers.set(action, handler);
    },
    setPositionState(state) {
      this.positionState = state;
    },
  };

  class MediaMetadata {
    constructor(init = {}) {
      this.title = init.title || "";
      this.artist = init.artist || "";
      this.album = init.album || "";
      this.artwork = init.artwork || [];
    }
  }

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
    navigator: { onLine: true, mediaSession },
    MediaMetadata,
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

test("player.js: Media Session metadata is assigned when an audio track plays", () => {
  const { mockWindow, mockDocument } = createMockDom({ lang: "en" });
  const context = vm.createContext({
    window: mockWindow,
    document: mockDocument,
    navigator: mockWindow.navigator,
    MediaMetadata: mockWindow.MediaMetadata,
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

  const table = mockDocument.createElement("table");
  table.classList.add("tracklist");
  const tr = mockDocument.createElement("tr");
  const td = mockDocument.createElement("td");
  const audio = mockDocument.createElement("audio");
  audio.setAttribute("data-track", "Test Track 1");
  audio.setAttribute("data-artist", "Test Band");
  audio.setAttribute("data-album", "Test Album");
  audio.setAttribute("data-artwork", "https://example.com/cover.jpg");
  td.appendChild(audio);
  tr.appendChild(td);
  table.appendChild(tr);
  mockDocument.body.appendChild(table);

  audio.play();

  const metadata = mockWindow.navigator.mediaSession.metadata;
  assert.ok(metadata);
  assert.equal(metadata.title, "Test Track 1");
  assert.equal(metadata.artist, "Test Band");
  assert.equal(metadata.album, "Test Album");
  assert.equal(metadata.artwork.length, 1);
  assert.equal(metadata.artwork[0].src, "https://example.com/cover.jpg");
  assert.equal(mockWindow.navigator.mediaSession.playbackState, "playing");

  audio.pause();
  assert.equal(mockWindow.navigator.mediaSession.playbackState, "paused");
});

test("player.js: Media Session action handlers control playback and navigation", () => {
  const { mockWindow, mockDocument } = createMockDom({ lang: "en" });
  const context = vm.createContext({
    window: mockWindow,
    document: mockDocument,
    navigator: mockWindow.navigator,
    MediaMetadata: mockWindow.MediaMetadata,
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

  const table = mockDocument.createElement("table");
  table.classList.add("tracklist");
  const audio1 = mockDocument.createElement("audio");
  audio1.setAttribute("data-track", "Track 1");
  const audio2 = mockDocument.createElement("audio");
  audio2.setAttribute("data-track", "Track 2");

  const tr1 = mockDocument.createElement("tr");
  tr1.appendChild(audio1);
  table.appendChild(tr1);

  const tr2 = mockDocument.createElement("tr");
  tr2.appendChild(audio2);
  table.appendChild(tr2);

  mockDocument.body.appendChild(table);

  // Check action handlers were registered
  const handlers = mockWindow.navigator.mediaSession._actionHandlers;
  assert.ok(handlers.has("play"));
  assert.ok(handlers.has("pause"));
  assert.ok(handlers.has("nexttrack"));
  assert.ok(handlers.has("previoustrack"));
  assert.ok(handlers.has("seekto"));

  // Play audio1
  audio1.play();
  assert.equal(audio1.paused, false);

  // Invoke pause handler
  handlers.get("pause")();
  assert.equal(audio1.paused, true);

  // Invoke play handler
  handlers.get("play")();
  assert.equal(audio1.paused, false);

  // Invoke nexttrack handler
  handlers.get("nexttrack")();
  assert.equal(audio1.paused, true);
  assert.equal(audio2.paused, false);

  // Invoke previoustrack handler with currentTime <= 3
  audio2.currentTime = 1;
  handlers.get("previoustrack")();
  assert.equal(audio2.paused, true);
  assert.equal(audio1.paused, false);

  // Seekto handler
  handlers.get("seekto")({ seekTime: 42 });
  assert.equal(audio1.currentTime, 42);
});

