// Unit tests for review_app/static/admin_users.js (GitHub issue #70).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const adminUsersJs = fs.readFileSync(path.join(__dirname, "static", "admin_users.js"), "utf-8");

class MockElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.attributes = new Map();
    this.listeners = new Map();
  }

  getAttribute(attr) {
    return this.attributes.get(attr) || null;
  }

  setAttribute(attr, val) {
    this.attributes.set(attr, String(val));
  }

  addEventListener(event, fn) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, []);
    }
    this.listeners.get(event).push(fn);
  }

  dispatchEvent(event) {
    const list = this.listeners.get(event.type) || [];
    for (const fn of list) {
      fn.call(this, event);
    }
  }
}

class MockDocument {
  constructor() {
    this.readyState = "complete";
    this.listeners = new Map();
    this.elements = [];
  }

  addEventListener(event, fn) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, []);
    }
    this.listeners.get(event).push(fn);
  }

  querySelectorAll(selector) {
    if (selector === "button[data-confirm]") {
      return this.elements.filter(
        (el) => el.tagName === "BUTTON" && el.attributes.has("data-confirm")
      );
    }
    return [];
  }
}

function createEnvironment(elements = []) {
  const doc = new MockDocument();
  doc.elements = elements;

  const confirmCalls = [];
  let confirmResult = true;

  const mockWindow = {
    confirm: (msg) => {
      confirmCalls.push(msg);
      return confirmResult;
    },
  };

  const sandbox = {
    document: doc,
    window: mockWindow,
    module: { exports: {} },
  };

  vm.createContext(sandbox);
  vm.runInContext(adminUsersJs, sandbox);

  return {
    doc,
    sandbox,
    confirmCalls,
    setConfirmResult: (val) => {
      confirmResult = val;
    },
  };
}

test("admin_users.js: cancels click event when user dismisses confirm dialog", () => {
  const btn = new MockElement("button");
  btn.setAttribute("data-confirm", "Deactivate test@example.com?");

  const env = createEnvironment([btn]);
  env.setConfirmResult(false);

  let prevented = false;
  const event = {
    type: "click",
    preventDefault: () => {
      prevented = true;
    },
  };

  btn.dispatchEvent(event);

  assert.equal(env.confirmCalls.length, 1);
  assert.equal(env.confirmCalls[0], "Deactivate test@example.com?");
  assert.equal(prevented, true, "preventDefault should be called when confirm is false");
});

test("admin_users.js: allows click event when user accepts confirm dialog", () => {
  const btn = new MockElement("button");
  btn.setAttribute("data-confirm", "Deactivate test@example.com?");

  const env = createEnvironment([btn]);
  env.setConfirmResult(true);

  let prevented = false;
  const event = {
    type: "click",
    preventDefault: () => {
      prevented = true;
    },
  };

  btn.dispatchEvent(event);

  assert.equal(env.confirmCalls.length, 1);
  assert.equal(env.confirmCalls[0], "Deactivate test@example.com?");
  assert.equal(prevented, false, "preventDefault should not be called when confirm is true");
});

test("admin_users.js: does not attach confirmation to buttons without data-confirm", () => {
  const normalBtn = new MockElement("button");
  const env = createEnvironment([normalBtn]);

  let prevented = false;
  const event = {
    type: "click",
    preventDefault: () => {
      prevented = true;
    },
  };

  normalBtn.dispatchEvent(event);

  assert.equal(env.confirmCalls.length, 0);
  assert.equal(prevented, false);
});
