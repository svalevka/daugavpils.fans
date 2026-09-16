// Unit tests for review_app/static/draft_recovery.js (GitHub issue #64).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const draftRecoveryJs = fs.readFileSync(path.join(__dirname, "static", "draft_recovery.js"), "utf-8");

class MockElement {
  constructor(tagName, id = "", name = "") {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.name = name;
    this.type = "";
    this.attributes = new Map();
    this.children = [];
    this.parentElement = null;
    this._textContent = "";
    this._value = "";
    this.checked = false;
    this.style = {};
    this.listeners = new Map();
    this.classList = {
      _classes: new Set(),
      add: (...cls) => cls.forEach((c) => this.classList._classes.add(c)),
      remove: (...cls) => cls.forEach((c) => this.classList._classes.delete(c)),
      contains: (c) => this.classList._classes.has(c),
    };
  }

  get textContent() {
    return this._textContent;
  }

  set textContent(val) {
    this._textContent = String(val);
  }

  get value() {
    return this._value;
  }

  set value(val) {
    this._value = val;
  }

  getAttribute(attr) {
    return this.attributes.get(attr) || null;
  }

  setAttribute(attr, val) {
    this.attributes.set(attr, String(val));
    if (attr === "id") this.id = val;
    if (attr === "name") this.name = val;
    if (attr === "type") this.type = val;
  }

  hasAttribute(attr) {
    return this.attributes.has(attr);
  }

  removeAttribute(attr) {
    this.attributes.delete(attr);
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(handler);
  }

  dispatchEvent(event) {
    event.target = event.target || this;
    let curr = this;
    while (curr) {
      const arr = curr.listeners.get(event.type) || [];
      event.currentTarget = curr;
      for (const h of arr) {
        h.call(curr, event);
      }
      if (!event.bubbles) break;
      curr = curr.parentElement;
    }
  }

  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }

  querySelectorAll(sel) {
    if (sel.includes(",")) {
      const parts = sel.split(",").map((s) => s.trim());
      const set = new Set();
      for (const p of parts) {
        for (const el of this.querySelectorAll(p)) set.add(el);
      }
      return Array.from(set);
    }
    const results = [];
    function match(node) {
      let isMatch = true;
      let s = sel;
      const tagMatch = s.match(/^([a-zA-Z0-9_-]+)/);
      if (tagMatch) {
        if (node.tagName.toLowerCase() !== tagMatch[1].toLowerCase()) {
          isMatch = false;
        }
        s = s.slice(tagMatch[1].length);
      }
      if (s.startsWith("#")) {
        if (node.id !== s.slice(1)) isMatch = false;
      } else if (s.startsWith(".")) {
        if (!node.classList.contains(s.slice(1))) isMatch = false;
      } else if (s.startsWith("[name=\"") && s.endsWith("\"]")) {
        if (node.name !== s.slice(7, -2)) isMatch = false;
      } else if (s.startsWith("[") && s.endsWith("]")) {
        if (!node.hasAttribute(s.slice(1, -1))) isMatch = false;
      }
      if (isMatch && (tagMatch || s.length > 0)) {
        results.push(node);
      }
      for (const ch of node.children) {
        match(ch);
      }
    }
    for (const ch of this.children) {
      match(ch);
    }
    return results;
  }

  get elements() {
    const list = [];
    function collect(node) {
      const tag = node.tagName.toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select" || tag === "button") {
        list.push(node);
        if (node.name) list[node.name] = node;
        if (node.id) list[node.id] = node;
      }
      for (const ch of node.children) collect(ch);
    }
    for (const ch of this.children) collect(ch);
    return list;
  }
}

function setupMockEnvironment(storageInitial = {}) {
  const root = new MockElement("body");
  const localStorageStore = new Map(Object.entries(storageInitial));

  const mockLocalStorage = {
    getItem(k) {
      return localStorageStore.has(k) ? localStorageStore.get(k) : null;
    },
    setItem(k, v) {
      localStorageStore.set(k, String(v));
    },
    removeItem(k) {
      localStorageStore.delete(k);
    },
    clear() {
      localStorageStore.clear();
    }
  };

  const docListeners = new Map();
  const documentMock = {
    readyState: "complete",
    createElement(tag) { return new MockElement(tag); },
    addEventListener(type, handler) {
      if (!docListeners.has(type)) docListeners.set(type, []);
      docListeners.get(type).push(handler);
    },
    querySelector(sel) { return root.querySelector(sel); },
    querySelectorAll(sel) { return root.querySelectorAll(sel); }
  };

  class MockEvent {
    constructor(type, opts = {}) {
      this.type = type;
      this.bubbles = opts.bubbles || false;
    }
  }

  const sandbox = {
    document: documentMock,
    localStorage: mockLocalStorage,
    window: {
      location: { pathname: "/submit/add-band" }
    },
    Event: MockEvent,
    setTimeout,
    clearTimeout,
    Date,
    JSON,
    Array,
    Object,
    String,
    Boolean,
    console
  };

  return { sandbox, root, mockLocalStorage, docListeners };
}

test("draft_recovery.js: auto-saves form fields and excludes sensitive/file fields", async () => {
  const { sandbox, root, mockLocalStorage } = setupMockEnvironment();

  const form = new MockElement("form");
  form.setAttribute("data-band-form", "true");
  form.setAttribute("data-draft-key", "add-band");

  const nameInput = new MockElement("input", "name", "name");
  nameInput.type = "text";
  form.appendChild(nameInput);

  const descInput = new MockElement("textarea", "description", "description");
  form.appendChild(descInput);

  const csrfInput = new MockElement("input", "csrf_token", "csrf_token");
  csrfInput.type = "hidden";
  csrfInput.value = "secret-token-12345";
  form.appendChild(csrfInput);

  const honeypotInput = new MockElement("input", "website", "website");
  honeypotInput.type = "text";
  honeypotInput.value = "bot-value";
  form.appendChild(honeypotInput);

  const fileInput = new MockElement("input", "photo", "photo");
  fileInput.type = "file";
  form.appendChild(fileInput);

  const releaseCheckbox = new MockElement("input", "has_release", "has_release");
  releaseCheckbox.type = "checkbox";
  form.appendChild(releaseCheckbox);

  root.appendChild(form);

  vm.runInNewContext(draftRecoveryJs, sandbox);

  // Type in band name and description
  nameInput.value = "Chemical Reaction";
  nameInput.dispatchEvent(new sandbox.Event("input", { bubbles: true }));

  descInput.value = "Recorded in Daugavpils DK Khimik, 1994.";
  descInput.dispatchEvent(new sandbox.Event("input", { bubbles: true }));

  releaseCheckbox.checked = true;
  releaseCheckbox.dispatchEvent(new sandbox.Event("change", { bubbles: true }));

  // Wait for debounce timer (400ms)
  await new Promise((resolve) => setTimeout(resolve, 500));

  const stored = mockLocalStorage.getItem("daugavpils-draft:add-band");
  assert.ok(stored, "Draft was saved to localStorage");

  const parsed = JSON.parse(stored);
  assert.equal(parsed.fields.name, "Chemical Reaction");
  assert.equal(parsed.fields.description, "Recorded in Daugavpils DK Khimik, 1994.");
  assert.equal(parsed.fields.has_release, true);

  // Sensitive & file fields must NOT be saved
  assert.equal(parsed.fields.csrf_token, undefined, "csrf_token excluded");
  assert.equal(parsed.fields.website, undefined, "website honeypot excluded");
  assert.equal(parsed.fields.photo, undefined, "file input excluded");
});

test("draft_recovery.js: restores draft on load, triggers events, and shows draft banner", async () => {
  const timestamp = Date.now() - 3600000;
  const initialStorage = {
    "daugavpils-draft:add-band": JSON.stringify({
      timestamp: timestamp,
      fields: {
        name: "Restored Band",
        description: "Restored biography text.",
        has_release: true
      }
    })
  };

  const { sandbox, root, mockLocalStorage } = setupMockEnvironment(initialStorage);

  const form = new MockElement("form");
  form.setAttribute("data-band-form", "true");
  form.setAttribute("data-draft-key", "add-band");
  form.setAttribute("data-msg-draft-restored", "Restored unsaved draft from {time}.");
  form.setAttribute("data-msg-draft-cleared", "Draft cleared.");

  const banner = new MockElement("div");
  banner.setAttribute("data-draft-banner", "true");
  banner.style.display = "none";
  form.appendChild(banner);

  const bannerText = new MockElement("span");
  bannerText.setAttribute("data-draft-text", "true");
  banner.appendChild(bannerText);

  const clearBtn = new MockElement("button");
  clearBtn.setAttribute("data-draft-clear", "true");
  banner.appendChild(clearBtn);

  const nameInput = new MockElement("input", "name", "name");
  nameInput.type = "text";
  form.appendChild(nameInput);

  const descInput = new MockElement("textarea", "description", "description");
  form.appendChild(descInput);

  const releaseCheckbox = new MockElement("input", "has_release", "has_release");
  releaseCheckbox.type = "checkbox";
  form.appendChild(releaseCheckbox);

  root.appendChild(form);

  let inputEventFired = false;
  nameInput.addEventListener("input", () => { inputEventFired = true; });

  let changeEventFired = false;
  releaseCheckbox.addEventListener("change", () => { changeEventFired = true; });

  vm.runInNewContext(draftRecoveryJs, sandbox);

  // Assert fields were populated
  assert.equal(nameInput.value, "Restored Band");
  assert.equal(descInput.value, "Restored biography text.");
  assert.equal(releaseCheckbox.checked, true);
  assert.equal(inputEventFired, true, "input event dispatched on restored element");
  assert.equal(changeEventFired, true, "change event dispatched on restored checkbox");

  // Assert draft banner is shown
  assert.equal(banner.style.display, "flex");
  assert.match(bannerText.textContent, /Restored unsaved draft from/);

  // Assert clicking clear draft removes draft and resets fields
  clearBtn.onclick({ preventDefault() {}, stopPropagation() {} });

  assert.equal(mockLocalStorage.getItem("daugavpils-draft:add-band"), null, "Draft purged from localStorage");
  assert.equal(nameInput.value, "");
  assert.equal(descInput.value, "");
  assert.equal(releaseCheckbox.checked, false);
  assert.equal(bannerText.textContent, "Draft cleared.");
});

test("draft_recovery.js: DraftRecovery.clear purges draft on successful submission", async () => {
  const initialStorage = {
    "daugavpils-draft:/submit/add-release": JSON.stringify({
      timestamp: Date.now(),
      fields: { name: "Completed Album", date_published: "1997" }
    })
  };

  const { sandbox, root, mockLocalStorage } = setupMockEnvironment(initialStorage);
  sandbox.window.location.pathname = "/submit/add-release";

  const form = new MockElement("form");
  form.setAttribute("data-album-form", "true");

  const banner = new MockElement("div");
  banner.setAttribute("data-draft-banner", "true");
  form.appendChild(banner);

  const nameInput = new MockElement("input", "name", "name");
  form.appendChild(nameInput);

  root.appendChild(form);

  vm.runInNewContext(draftRecoveryJs, sandbox);

  assert.equal(nameInput.value, "Completed Album");

  // Simulate submission completion
  sandbox.window.DraftRecovery.clear(form);

  assert.equal(mockLocalStorage.getItem("daugavpils-draft:/submit/add-release"), null);
  assert.equal(banner.style.display, "none");
});
