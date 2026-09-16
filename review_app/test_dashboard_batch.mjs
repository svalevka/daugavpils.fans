// Unit tests for review_app/static/dashboard_batch.js (GitHub issue #66).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const batchJs = fs.readFileSync(path.join(__dirname, "static", "dashboard_batch.js"), "utf-8");

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
    this.indeterminate = false;
    this.disabled = false;
    this.title = "";
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
    return this.attributes.get("value") || this._value;
  }

  set value(val) {
    this._value = String(val);
    this.attributes.set("value", String(val));
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

  removeAttribute(attr) {
    this.attributes.delete(attr);
    if (attr === "title") this.title = "";
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
      fn(event);
    }
    return !event.defaultPrevented;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    const matches = [];
    const check = (node) => {
      if (matchesSelector(node, selector)) {
        matches.push(node);
      }
      for (const child of node.children) {
        check(child);
      }
    };
    for (const child of this.children) {
      check(child);
    }
    return matches;
  }

  appendChild(node) {
    node.parentElement = this;
    this.children.push(node);
    return node;
  }
}

function matchesSelector(node, selector) {
  if (!node || !selector) return false;
  if (selector.startsWith(".")) {
    return node.classList.contains(selector.slice(1));
  }
  if (selector.startsWith("#")) {
    return node.id === selector.slice(1);
  }
  if (selector.includes("[") && selector.endsWith("]")) {
    const attrMatch = selector.match(/\[([^=\]]+)(?:="?([^"\]]*)"?)?\]/);
    if (attrMatch) {
      const attr = attrMatch[1];
      const val = attrMatch[2];
      const actual = node.getAttribute(attr);
      if (val !== undefined) {
        return actual === val;
      }
      return actual !== null;
    }
  }
  return node.tagName.toLowerCase() === selector.toLowerCase();
}

function createDOMEnvironment() {
  const docListeners = new Map();
  const body = new MockElement("body");
  const alerts = [];

  const document = {
    body,
    addEventListener(event, fn) {
      if (!docListeners.has(event)) {
        docListeners.set(event, []);
      }
      docListeners.get(event).push(fn);
    },
    dispatchEvent(event) {
      const list = docListeners.get(event.type) || [];
      for (const fn of list) {
        fn(event);
      }
      return !event.defaultPrevented;
    },
    querySelectorAll(selector) {
      const matches = [];
      const check = (node) => {
        if (selector === ".batch-action-bar" && node.classList.contains("batch-action-bar")) {
          matches.push(node);
        } else if (selector.includes(".proposal-select") && node.classList.contains("proposal-select")) {
          if (selector.includes('data-type="text"') && node.getAttribute("data-type") === "text") {
            matches.push(node);
          } else if (selector.includes('data-type="media"') && node.getAttribute("data-type") === "media") {
            matches.push(node);
          } else if (!selector.includes("data-type")) {
            matches.push(node);
          }
        }
        for (const child of node.children) {
          check(child);
        }
      };
      check(body);
      return matches;
    },
    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    },
  };

  const window = {
    document,
    alert(msg) {
      alerts.push(msg);
    },
  };

  return { document, window, alerts };
}

test("dashboard_batch.js: selection, counts, anti-self-approval, and form submit validation", () => {
  const { document, window, alerts } = createDOMEnvironment();

  // Create batch form
  const form = new MockElement("form");
  form.classList.add("batch-action-bar");

  const typeInput = new MockElement("input");
  typeInput.setAttribute("name", "proposal_type");
  typeInput.value = "text";
  form.appendChild(typeInput);

  const selectAll = new MockElement("input");
  selectAll.setAttribute("data-select-all", "text");
  form.appendChild(selectAll);

  const countBadge = new MockElement("span");
  countBadge.setAttribute("data-selected-count", "text");
  form.appendChild(countBadge);

  const approveBtn = new MockElement("button");
  approveBtn.setAttribute("name", "action");
  approveBtn.value = "approve";
  form.appendChild(approveBtn);

  const rejectBtn = new MockElement("button");
  rejectBtn.setAttribute("name", "action");
  rejectBtn.value = "reject";
  form.appendChild(rejectBtn);

  document.body.appendChild(form);

  // Create 3 proposal checkboxes: 2 normal, 1 own submission
  const cb1 = new MockElement("input");
  cb1.classList.add("proposal-select");
  cb1.setAttribute("data-type", "text");
  cb1.setAttribute("data-own", "0");
  cb1.value = "101";
  document.body.appendChild(cb1);

  const cb2 = new MockElement("input");
  cb2.classList.add("proposal-select");
  cb2.setAttribute("data-type", "text");
  cb2.setAttribute("data-own", "0");
  cb2.value = "102";
  document.body.appendChild(cb2);

  const cbOwn = new MockElement("input");
  cbOwn.classList.add("proposal-select");
  cbOwn.setAttribute("data-type", "text");
  cbOwn.setAttribute("data-own", "1");
  cbOwn.value = "103";
  document.body.appendChild(cbOwn);

  // Execute script in sandbox
  const context = vm.createContext({
    document,
    window,
    alert: window.alert,
    Array,
  });
  vm.runInContext(batchJs, context);

  // Trigger DOMContentLoaded
  document.dispatchEvent({ type: "DOMContentLoaded" });

  // Initial state: 0 selected, buttons disabled
  assert.equal(countBadge.textContent, "(0 selected)");
  assert.equal(approveBtn.disabled, true);
  assert.equal(rejectBtn.disabled, true);
  assert.equal(selectAll.checked, false);
  assert.equal(selectAll.indeterminate, false);

  // 1. Select cb1
  cb1.checked = true;
  document.dispatchEvent({ type: "change", target: cb1 });

  assert.equal(countBadge.textContent, "(1 selected)");
  assert.equal(approveBtn.disabled, false);
  assert.equal(rejectBtn.disabled, false);
  assert.equal(selectAll.indeterminate, true);

  // 2. Select cbOwn (self-submission)
  cbOwn.checked = true;
  document.dispatchEvent({ type: "change", target: cbOwn });

  assert.equal(countBadge.textContent, "(2 selected)");
  // Anti-self-approval: approve button should be disabled with tooltip
  assert.equal(approveBtn.disabled, true);
  assert.equal(approveBtn.title, "Cannot approve self-submitted proposal");
  // Rejecting own proposal is allowed
  assert.equal(rejectBtn.disabled, false);

  // Form submit prevention when self-submission is checked
  let submitPrevented = false;
  const approveSubmitEvent = {
    type: "submit",
    submitter: approveBtn,
    preventDefault: () => {
      submitPrevented = true;
    },
  };
  form.dispatchEvent(approveSubmitEvent);
  assert.equal(submitPrevented, true);
  assert.equal(alerts.length, 1);
  assert.match(alerts[0], /cannot approve your own submission/i);

  // 3. Uncheck cbOwn
  cbOwn.checked = false;
  document.dispatchEvent({ type: "change", target: cbOwn });

  assert.equal(countBadge.textContent, "(1 selected)");
  assert.equal(approveBtn.disabled, false);
  assert.equal(approveBtn.title, "");

  // 4. Test "Select All"
  selectAll.checked = true;
  selectAll.dispatchEvent({ type: "change" });

  assert.equal(cb1.checked, true);
  assert.equal(cb2.checked, true);
  assert.equal(cbOwn.checked, true);
  assert.equal(countBadge.textContent, "(3 selected)");
  // Contains cbOwn, so approveBtn must be disabled
  assert.equal(approveBtn.disabled, true);

  // Deselect all
  selectAll.checked = false;
  selectAll.dispatchEvent({ type: "change" });

  assert.equal(cb1.checked, false);
  assert.equal(cb2.checked, false);
  assert.equal(cbOwn.checked, false);
  assert.equal(countBadge.textContent, "(0 selected)");
  assert.equal(approveBtn.disabled, true);
  assert.equal(rejectBtn.disabled, true);

  // 5. Submit with 0 selected
  submitPrevented = false;
  form.dispatchEvent({
    type: "submit",
    submitter: approveBtn,
    preventDefault: () => {
      submitPrevented = true;
    },
  });
  assert.equal(submitPrevented, true);
  assert.equal(alerts.length, 2);
  assert.match(alerts[1], /select at least one proposal/i);
});
