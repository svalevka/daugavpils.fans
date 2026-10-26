// Unit tests for review_app/static/album_upload.js and band_upload.js
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const albumUploadJs = fs.readFileSync(path.join(__dirname, "static", "album_upload.js"), "utf-8");
const bandUploadJs = fs.readFileSync(path.join(__dirname, "static", "band_upload.js"), "utf-8");

class MockElement {
  constructor(tagName, id = "") {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.attributes = new Map();
    this.children = [];
    this.parentElement = null;
    this._textContent = "";
    this._value = "";
    this.disabled = false;
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

  get className() {
    return Array.from(this.classList._classes).join(" ");
  }

  set className(val) {
    this.classList._classes.clear();
    String(val)
      .split(/\s+/)
      .filter(Boolean)
      .forEach((c) => this.classList._classes.add(c));
  }

  get textContent() {
    return this._textContent;
  }

  set textContent(val) {
    this._textContent = String(val);
  }

  get value() {
    if (this.tagName === "PROGRESS") return Number(this._value);
    return this._value;
  }

  set value(val) {
    this._value = val;
  }

  get innerHTML() {
    return "";
  }

  set innerHTML(val) {
    if (val === "") {
      this.children = [];
    }
  }

  getAttribute(attr) {
    return this.attributes.get(attr) || null;
  }

  setAttribute(attr, val) {
    this.attributes.set(attr, String(val));
    if (attr === "id") this.id = val;
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

  removeChild(child) {
    const idx = this.children.indexOf(child);
    if (idx !== -1) {
      this.children.splice(idx, 1);
      child.parentElement = null;
    }
    return child;
  }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, []);
    }
    this.listeners.get(type).push(handler);
  }

  removeEventListener(type, handler) {
    const arr = this.listeners.get(type);
    if (arr) {
      const idx = arr.indexOf(handler);
      if (idx !== -1) arr.splice(idx, 1);
    }
  }

  dispatchEvent(event) {
    const arr = this.listeners.get(event.type) || [];
    event.target = event.target || this;
    event.currentTarget = this;
    for (const h of arr) {
      h.call(this, event);
    }
  }

  click() {
    this.dispatchEvent({ type: "click", preventDefault() {}, stopPropagation() {} });
  }

  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }

  querySelectorAll(sel) {
    const results = [];
    function match(node) {
      let isMatch = false;
      if (sel.startsWith("#") && node.id === sel.slice(1)) isMatch = true;
      if (sel.startsWith(".") && node.classList.contains(sel.slice(1))) isMatch = true;
      if (sel.startsWith("[") && sel.endsWith("]")) {
        const attrName = sel.slice(1, -1);
        if (node.hasAttribute(attrName)) isMatch = true;
      }
      if (node.tagName.toLowerCase() === sel.toLowerCase()) isMatch = true;

      if (isMatch) results.push(node);
      for (const ch of node.children) {
        match(ch);
      }
    }
    for (const ch of this.children) {
      match(ch);
    }
    return results;
  }
}

function setupMockEnvironment(formHtml) {
  const root = new MockElement("body");

  // Factory for elements
  function createElement(tag) {
    return new MockElement(tag);
  }

  // Audio mock
  class MockAudio {
    constructor() {
      this.src = "";
      this.paused = true;
      this.listeners = new Map();
    }
    addEventListener(type, handler) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(handler);
    }
    play() {
      this.paused = false;
      return Promise.resolve();
    }
    pause() {
      this.paused = true;
    }
    load() {}
    removeAttribute(attr) {
      if (attr === "src") this.src = "";
    }
    _trigger(type) {
      const arr = this.listeners.get(type) || [];
      for (const h of arr) h({ type });
    }
  }

  let lastXHR = null;
  class MockXHR {
    constructor() {
      this.status = 0;
      this.responseText = "";
      this.upload = {
        listeners: new Map(),
        addEventListener(type, handler) {
          if (!this.listeners.has(type)) this.listeners.set(type, []);
          this.listeners.get(type).push(handler);
        },
        _trigger(type, evt) {
          const arr = this.listeners.get(type) || [];
          for (const h of arr) h(evt);
        }
      };
      lastXHR = this;
    }
    open(method, url) {
      this.method = method;
      this.url = url;
    }
    send(formData) {
      this.sentData = formData;
    }
  }

  class MockFormData {
    constructor(form) {
      this.entries = [];
    }
    append(key, val, filename) {
      this.entries.push({ key, val, filename });
    }
    delete(key) {
      this.entries = this.entries.filter((e) => e.key !== key);
    }
  }

  let objectUrlCounter = 1;
  const objectUrls = new Set();
  const mockURL = {
    createObjectURL(blob) {
      const id = "blob:test/" + objectUrlCounter++;
      objectUrls.add(id);
      return id;
    },
    revokeObjectURL(url) {
      objectUrls.delete(url);
    }
  };

  const docListeners = new Map();
  const documentMock = {
    createElement,
    addEventListener(type, handler) {
      if (!docListeners.has(type)) docListeners.set(type, []);
      docListeners.get(type).push(handler);
    },
    querySelector(sel) {
      return root.querySelector(sel);
    },
    querySelectorAll(sel) {
      return root.querySelectorAll(sel);
    },
    open() { this._open = true; },
    write(html) { this._writtenHtml = html; },
    close() { this._closed = true; }
  };

  const sandbox = {
    document: documentMock,
    Audio: MockAudio,
    XMLHttpRequest: MockXHR,
    FormData: MockFormData,
    URL: mockURL,
    FileReader: class {
      readAsDataURL(file) {
        this.result = "data:image/jpeg;base64,mock";
        if (this.onload) this.onload({ target: { result: this.result } });
      }
    },
    alert(msg) { sandbox.lastAlert = msg; },
    Array,
    String,
    Math,
    Promise,
  };

  return { sandbox, root, docListeners, getLastXHR: () => lastXHR, objectUrls };
}

test("album_upload.js: audio preview, drag-and-drop, and upload progress", async (t) => {
  const { sandbox, root, docListeners, getLastXHR, objectUrls } = setupMockEnvironment();

  // Construct album form
  const form = new MockElement("form");
  form.setAttribute("data-album-form", "true");
  form.setAttribute("action", "/submit-release");
  form.setAttribute("data-msg-uploading", "Uploading... {percent}% ({loaded} / {total})");
  form.setAttribute("data-msg-processing", "Processing files on server...");
  form.setAttribute("data-msg-network-error", "Network error during upload.");
  form.setAttribute("data-msg-rate-limit", "Too many submissions.");
  form.setAttribute("data-msg-server-error", "Server error ({status}).");
  form.setAttribute("data-msg-preview", "Preview track");
  form.setAttribute("data-msg-pause", "Pause");

  const nameInput = new MockElement("input", "name");
  nameInput.value = "";
  form.appendChild(nameInput);

  const yearInput = new MockElement("input", "date_published");
  yearInput.value = "";
  form.appendChild(yearInput);

  const audioInput = new MockElement("input");
  audioInput.setAttribute("data-album-audio-input", "true");
  audioInput.files = [];
  form.appendChild(audioInput);

  const addAudioBtn = new MockElement("button");
  addAudioBtn.setAttribute("data-album-add-audio", "true");
  form.appendChild(addAudioBtn);

  const dropzone = new MockElement("div");
  dropzone.setAttribute("data-album-dropzone", "true");
  form.appendChild(dropzone);

  const trackList = new MockElement("div");
  trackList.setAttribute("data-album-track-list", "true");
  form.appendChild(trackList);

  const progressContainer = new MockElement("div");
  progressContainer.setAttribute("data-album-progress", "true");
  progressContainer.style.display = "none";
  form.appendChild(progressContainer);

  const progressBar = new MockElement("progress");
  progressBar.setAttribute("data-album-progress-bar", "true");
  progressBar.value = 0;
  progressContainer.appendChild(progressBar);

  const progressStatus = new MockElement("div");
  progressStatus.setAttribute("data-album-progress-status", "true");
  progressContainer.appendChild(progressStatus);

  const errorBanner = new MockElement("div");
  errorBanner.setAttribute("data-album-upload-error", "true");
  errorBanner.style.display = "none";
  form.appendChild(errorBanner);

  const submitBtn = new MockElement("button");
  submitBtn.setAttribute("data-album-submit", "true");
  submitBtn.setAttribute("data-original-text", "Submit for review");
  submitBtn.textContent = "Submit for review";
  form.appendChild(submitBtn);

  root.appendChild(form);

  // Execute album_upload.js in sandbox
  vm.runInNewContext(albumUploadJs, sandbox);
  // Trigger DOMContentLoaded
  (docListeners.get("DOMContentLoaded") || []).forEach((h) => h());

  // Initially submit is disabled because no tracks, no name, no year
  assert.equal(submitBtn.disabled, true);

  // Fill in name and year
  nameInput.value = "Test Album";
  nameInput.dispatchEvent({ type: "input" });
  yearInput.value = "1996";
  yearInput.dispatchEvent({ type: "input" });
  assert.equal(submitBtn.disabled, true, "Submit still disabled without tracks");

  // 1. Add audio files via dropzone
  const mockFile1 = { name: "01 - Intro Track.mp3", type: "audio/mpeg", size: 5000000 };
  const mockFile2 = { name: "02. Second Song.wav", type: "audio/wav", size: 8000000 };

  dropzone.dispatchEvent({
    type: "drop",
    preventDefault() {},
    stopPropagation() {},
    dataTransfer: { files: [mockFile1, mockFile2] }
  });

  // Track list should now have 2 rows
  assert.equal(trackList.children.length, 2);
  assert.equal(submitBtn.disabled, false, "Submit enabled when title, year, and tracks are present");

  const row1 = trackList.children[0];
  const row2 = trackList.children[1];

  // Cleaned title assertions
  const title1 = row1.querySelector(".track-title-input");
  assert.equal(title1.value, "Intro Track");
  const title2 = row2.querySelector(".track-title-input");
  assert.equal(title2.value, "Second Song");

  // 2. Audio preview test
  const previewBtn1 = row1.querySelector(".btn-preview");
  assert.ok(previewBtn1);
  assert.equal(previewBtn1.textContent, "▶");

  // Click play on track 1
  previewBtn1.click();
  assert.equal(previewBtn1.textContent, "⏸");
  assert.equal(previewBtn1.classList.contains("playing"), true);

  // Click pause on track 1
  previewBtn1.click();
  assert.equal(previewBtn1.textContent, "▶");
  assert.equal(previewBtn1.classList.contains("playing"), false);

  // Click play again on track 1, then click track 2
  previewBtn1.click();
  assert.equal(previewBtn1.textContent, "⏸");

  const previewBtn2 = row2.querySelector(".btn-preview");
  previewBtn2.click();
  assert.equal(previewBtn1.textContent, "▶");
  assert.equal(previewBtn2.textContent, "⏸");
  assert.equal(previewBtn2.classList.contains("playing"), true);

  // 3. Track reordering via drag & drop
  // Drag row 1 to row 2
  row1.dispatchEvent({
    type: "dragstart",
    dataTransfer: { effectAllowed: "", setData() {} }
  });
  row2.dispatchEvent({
    type: "drop",
    preventDefault() {},
    dataTransfer: {}
  });

  // After reordering, Second Song should now be row 1, Intro Track should be row 2
  assert.equal(trackList.children[0].querySelector(".track-title-input").value, "Second Song");
  assert.equal(trackList.children[1].querySelector(".track-title-input").value, "Intro Track");

  // 4. Form submission with upload progress
  let defaultPrevented = false;
  form.dispatchEvent({
    type: "submit",
    preventDefault() { defaultPrevented = true; }
  });
  assert.equal(defaultPrevented, true);
  assert.equal(submitBtn.disabled, true);
  assert.equal(progressContainer.style.display, "block");
  assert.equal(progressBar.value, 0);

  const xhr = getLastXHR();
  assert.ok(xhr, "XHR request was dispatched");
  assert.equal(xhr.method, "POST");
  assert.equal(xhr.url, "/submit-release");

  // Simulate progress event
  xhr.upload._trigger("progress", {
    lengthComputable: true,
    loaded: 6500000,
    total: 13000000
  });

  assert.equal(progressBar.value, 50);
  assert.match(progressStatus.textContent, /50%/);
  assert.match(progressStatus.textContent, /6\.2 MB/);

  // Simulate upload complete -> processing
  xhr.upload._trigger("load", {});
  assert.equal(progressBar.value, 100);
  assert.equal(progressStatus.textContent, "Processing files on server...");

  // 5. Network error recovery
  xhr.onerror();
  assert.equal(progressContainer.style.display, "none");
  assert.equal(errorBanner.style.display, "block");
  assert.equal(errorBanner.textContent, "Network error during upload.");
  assert.equal(submitBtn.disabled, false);
  assert.equal(submitBtn.textContent, "Submit for review");
  assert.equal(trackList.children.length, 2, "Tracks preserved after error");
});

test("band_upload.js: pre-submission preview, dropzone, and progress error recovery", async (t) => {
  const { sandbox, root, docListeners, getLastXHR } = setupMockEnvironment();

  const form = new MockElement("form");
  form.setAttribute("data-band-form", "true");
  form.setAttribute("action", "/submit-band");
  form.setAttribute("data-msg-uploading", "Uploading... {percent}% ({loaded} / {total})");
  form.setAttribute("data-msg-processing", "Processing files on server...");
  form.setAttribute("data-msg-network-error", "Network error during upload.");
  form.setAttribute("data-msg-server-error", "Server error ({status}).");
  form.setAttribute("data-msg-rate-limit", "Too many submissions.");

  const nameInput = new MockElement("input", "name");
  nameInput.value = "New Band";
  form.appendChild(nameInput);

  const releaseToggle = new MockElement("input");
  releaseToggle.setAttribute("data-band-has-release-toggle", "true");
  releaseToggle.checked = true;
  form.appendChild(releaseToggle);

  const releaseSection = new MockElement("div");
  releaseSection.setAttribute("data-band-release-section", "true");
  form.appendChild(releaseSection);

  const relName = new MockElement("input", "release_name");
  relName.value = "Demo tape";
  releaseSection.appendChild(relName);

  const relYear = new MockElement("input", "release_date_published");
  relYear.value = "1994";
  releaseSection.appendChild(relYear);

  const audioInput = new MockElement("input");
  audioInput.setAttribute("data-band-audio-input", "true");
  audioInput.files = [];
  releaseSection.appendChild(audioInput);

  const addAudioBtn = new MockElement("button");
  addAudioBtn.setAttribute("data-band-add-audio", "true");
  releaseSection.appendChild(addAudioBtn);

  const dropzone = new MockElement("div");
  dropzone.setAttribute("data-band-dropzone", "true");
  releaseSection.appendChild(dropzone);

  const trackList = new MockElement("div");
  trackList.setAttribute("data-band-track-list", "true");
  releaseSection.appendChild(trackList);

  const progressContainer = new MockElement("div");
  progressContainer.setAttribute("data-band-progress", "true");
  progressContainer.style.display = "none";
  releaseSection.appendChild(progressContainer);

  const progressBar = new MockElement("progress");
  progressBar.setAttribute("data-band-progress-bar", "true");
  progressBar.value = 0;
  progressContainer.appendChild(progressBar);

  const progressStatus = new MockElement("div");
  progressStatus.setAttribute("data-band-progress-status", "true");
  progressContainer.appendChild(progressStatus);

  const errorBanner = new MockElement("div");
  errorBanner.setAttribute("data-band-upload-error", "true");
  errorBanner.style.display = "none";
  form.appendChild(errorBanner);

  const submitBtn = new MockElement("button");
  submitBtn.setAttribute("data-band-submit", "true");
  submitBtn.setAttribute("data-original-text", "Submit band");
  submitBtn.textContent = "Submit band";
  form.appendChild(submitBtn);

  root.appendChild(form);

  vm.runInNewContext(bandUploadJs, sandbox);
  (docListeners.get("DOMContentLoaded") || []).forEach((h) => h());

  // Drop audio file onto dropzone
  const trackFile = { name: "demo-track.flac", type: "audio/flac", size: 10000000 };
  dropzone.dispatchEvent({
    type: "drop",
    preventDefault() {},
    stopPropagation() {},
    dataTransfer: { files: [trackFile] }
  });

  assert.equal(trackList.children.length, 1);
  const trackRow = trackList.children[0];
  const previewBtn = trackRow.querySelector(".btn-preview");
  assert.ok(previewBtn);

  // Audio preview play/pause
  previewBtn.click();
  assert.equal(previewBtn.textContent, "⏸");
  assert.equal(previewBtn.classList.contains("playing"), true);

  previewBtn.click();
  assert.equal(previewBtn.textContent, "▶");
  assert.equal(previewBtn.classList.contains("playing"), false);

  // Submit with release
  form.dispatchEvent({
    type: "submit",
    preventDefault() {}
  });

  assert.equal(submitBtn.disabled, true);
  assert.equal(progressContainer.style.display, "block");

  const xhr = getLastXHR();
  assert.ok(xhr);

  // Progress update
  xhr.upload._trigger("progress", {
    lengthComputable: true,
    loaded: 5000000,
    total: 10000000
  });
  assert.equal(progressBar.value, 50);

  // Server error response (status 500)
  xhr.status = 500;
  xhr.onload();
  assert.equal(progressContainer.style.display, "none");
  assert.equal(errorBanner.style.display, "block");
  assert.equal(errorBanner.textContent, "Server error (500).");
  assert.equal(submitBtn.disabled, false);
});

test("album_upload.js: track removal, audio ended, 429 rate limit, and non-audio filtering", async (t) => {
  const { sandbox, root, docListeners, getLastXHR } = setupMockEnvironment();

  const form = new MockElement("form");
  form.setAttribute("data-album-form", "true");
  form.setAttribute("action", "/submit-release");
  form.setAttribute("data-msg-rate-limit", "Rate limit exceeded.");

  const nameInput = new MockElement("input", "name");
  nameInput.value = "Album Name";
  form.appendChild(nameInput);

  const yearInput = new MockElement("input", "date_published");
  yearInput.value = "2000";
  form.appendChild(yearInput);

  const audioInput = new MockElement("input");
  audioInput.setAttribute("data-album-audio-input", "true");
  audioInput.files = [];
  form.appendChild(audioInput);

  const addAudioBtn = new MockElement("button");
  addAudioBtn.setAttribute("data-album-add-audio", "true");
  form.appendChild(addAudioBtn);

  const dropzone = new MockElement("div");
  dropzone.setAttribute("data-album-dropzone", "true");
  form.appendChild(dropzone);

  const trackList = new MockElement("div");
  trackList.setAttribute("data-album-track-list", "true");
  form.appendChild(trackList);

  const progressContainer = new MockElement("div");
  progressContainer.setAttribute("data-album-progress", "true");
  progressContainer.style.display = "none";
  form.appendChild(progressContainer);

  const progressBar = new MockElement("progress");
  progressBar.setAttribute("data-album-progress-bar", "true");
  progressBar.value = 0;
  progressContainer.appendChild(progressBar);

  const progressStatus = new MockElement("div");
  progressStatus.setAttribute("data-album-progress-status", "true");
  progressContainer.appendChild(progressStatus);

  const errorBanner = new MockElement("div");
  errorBanner.setAttribute("data-album-upload-error", "true");
  errorBanner.style.display = "none";
  form.appendChild(errorBanner);

  const submitBtn = new MockElement("button");
  submitBtn.setAttribute("data-album-submit", "true");
  form.appendChild(submitBtn);

  root.appendChild(form);

  vm.runInNewContext(albumUploadJs, sandbox);
  (docListeners.get("DOMContentLoaded") || []).forEach((h) => h());

  // Drop non-audio file: should be ignored
  dropzone.dispatchEvent({
    type: "drop",
    preventDefault() {},
    stopPropagation() {},
    dataTransfer: { files: [{ name: "readme.txt", type: "text/plain" }] }
  });
  assert.equal(trackList.children.length, 0, "Non-audio files are ignored");

  // Drop valid audio file
  dropzone.dispatchEvent({
    type: "drop",
    preventDefault() {},
    stopPropagation() {},
    dataTransfer: { files: [{ name: "song.mp3", type: "audio/mpeg" }] }
  });
  assert.equal(trackList.children.length, 1);
  assert.equal(submitBtn.disabled, false);

  const row = trackList.children[0];
  const previewBtn = row.querySelector(".btn-preview");
  previewBtn.click();
  assert.equal(previewBtn.textContent, "⏸");

  // Remove track while playing
  const removeBtn = row.querySelector(".btn-remove");
  removeBtn.click();
  assert.equal(trackList.children.length, 0, "Track was removed");
  assert.equal(submitBtn.disabled, true, "Submit button disabled when no tracks remain");

  // Add another track
  dropzone.dispatchEvent({
    type: "drop",
    preventDefault() {},
    stopPropagation() {},
    dataTransfer: { files: [{ name: "another.ogg", type: "audio/ogg" }] }
  });
  assert.equal(trackList.children.length, 1);
  assert.equal(submitBtn.disabled, false);

  // Submit and test 429 rate limit error
  form.dispatchEvent({
    type: "submit",
    preventDefault() {}
  });
  const xhr = getLastXHR();
  xhr.status = 429;
  xhr.onload();
  assert.equal(errorBanner.style.display, "block");
  assert.equal(errorBanner.textContent, "Rate limit exceeded.");
  assert.equal(submitBtn.disabled, false);
});

test("album_upload.js: client-side size limits immediately warn and block submission, and parses server error JSON", () => {
  const { root, sandbox, docListeners, getLastXHR } = setupMockEnvironment();

  const form = new MockElement("form");
  form.setAttribute("data-album-form", "true");
  form.setAttribute("action", "/submit-release");
  form.setAttribute("data-msg-uploading", "Uploading... {percent}% ({loaded} / {total})");
  form.setAttribute("data-msg-track-too-large", "Track '{name}' ({size}) exceeds {max}.");
  form.setAttribute("data-msg-album-too-large", "Total audio size ({size}) exceeds {max}.");
  form.setAttribute("data-msg-cover-too-large", "Cover image ({size}) exceeds {max}.");
  form.setAttribute("data-msg-server-error", "Server error ({status}).");
  form.setAttribute("data-max-track-bytes", String(50 * 1024 * 1024));
  form.setAttribute("data-max-album-bytes", String(350 * 1024 * 1024));
  form.setAttribute("data-max-cover-bytes", String(15 * 1024 * 1024));

  const nameInput = new MockElement("input", "name");
  nameInput.value = "Great Album";
  form.appendChild(nameInput);

  const yearInput = new MockElement("input", "date_published");
  yearInput.value = "1996";
  form.appendChild(yearInput);

  const coverInput = new MockElement("input", "cover");
  coverInput.setAttribute("data-album-cover-input", "true");
  coverInput.files = [];
  form.appendChild(coverInput);

  const coverPreview = new MockElement("div");
  coverPreview.setAttribute("data-album-cover-preview", "true");
  form.appendChild(coverPreview);

  const audioInput = new MockElement("input");
  audioInput.setAttribute("data-album-audio-input", "true");
  audioInput.files = [];
  form.appendChild(audioInput);

  const addAudioBtn = new MockElement("button");
  addAudioBtn.setAttribute("data-album-add-audio", "true");
  form.appendChild(addAudioBtn);

  const dropzone = new MockElement("div");
  dropzone.setAttribute("data-album-dropzone", "true");
  form.appendChild(dropzone);

  const trackList = new MockElement("div");
  trackList.setAttribute("data-album-track-list", "true");
  form.appendChild(trackList);

  const progressContainer = new MockElement("div");
  progressContainer.setAttribute("data-album-progress", "true");
  progressContainer.style.display = "none";
  form.appendChild(progressContainer);

  const errorBanner = new MockElement("div");
  errorBanner.setAttribute("data-album-upload-error", "true");
  errorBanner.style.display = "none";
  form.appendChild(errorBanner);

  const submitBtn = new MockElement("button");
  submitBtn.setAttribute("data-album-submit", "true");
  form.appendChild(submitBtn);

  root.appendChild(form);

  vm.runInNewContext(albumUploadJs, sandbox);
  (docListeners.get("DOMContentLoaded") || []).forEach((h) => h());

  // 1. Single track exceeding 50 MB
  const bigTrack = { name: "oversized.wav", type: "audio/wav", size: 60 * 1024 * 1024 };
  dropzone.dispatchEvent({
    type: "drop",
    preventDefault() {},
    stopPropagation() {},
    dataTransfer: { files: [bigTrack] }
  });

  assert.equal(trackList.children.length, 1);
  assert.equal(errorBanner.style.display, "block", "Error banner shown for oversized track");
  assert.match(errorBanner.textContent, /Track 'oversized\.wav' \(60\.0 MB\) exceeds 50\.0 MB\./);
  assert.equal(submitBtn.disabled, true, "Submit button disabled when track is oversized");

  // Remove the oversized track
  const removeBtn = trackList.children[0].querySelector(".btn-remove");
  removeBtn.click();
  assert.equal(trackList.children.length, 0);
  assert.equal(errorBanner.style.display, "none", "Error banner cleared after removing oversized track");

  // 2. Multiple tracks within individual limit but exceeding total 350 MB limit (e.g. 10 x 40MB = 400MB)
  const tracksBatch = [];
  for (let i = 1; i <= 10; i++) {
    tracksBatch.push({ name: `track_${i}.wav`, type: "audio/wav", size: 40 * 1024 * 1024 });
  }
  dropzone.dispatchEvent({
    type: "drop",
    preventDefault() {},
    stopPropagation() {},
    dataTransfer: { files: tracksBatch }
  });

  assert.equal(trackList.children.length, 10);
  assert.equal(errorBanner.style.display, "block", "Error banner shown for album exceeding 350 MB");
  assert.match(errorBanner.textContent, /Total audio size \(400\.0 MB\) exceeds 350\.0 MB\./);
  assert.equal(submitBtn.disabled, true, "Submit button disabled when album exceeds 350 MB");

  // Remove two tracks (8 x 40 = 320 MB < 350 MB)
  trackList.children[9].querySelector(".btn-remove").click();
  trackList.children[8].querySelector(".btn-remove").click();
  assert.equal(trackList.children.length, 8);
  assert.equal(errorBanner.style.display, "none", "Error banner cleared once below 350 MB");
  assert.equal(submitBtn.disabled, false, "Submit button re-enabled");

  // 3. Oversized cover image (20 MB > 15 MB)
  coverInput.files = [{ name: "huge_cover.jpg", size: 20 * 1024 * 1024 }];
  coverInput.dispatchEvent({ type: "change" });
  assert.equal(errorBanner.style.display, "block", "Error banner shown for oversized cover");
  assert.match(errorBanner.textContent, /Cover image \(20\.0 MB\) exceeds 15\.0 MB\./);
  assert.equal(submitBtn.disabled, true, "Submit disabled with oversized cover");

  // Valid cover (2 MB)
  coverInput.files = [{ name: "cover.jpg", size: 2 * 1024 * 1024 }];
  coverInput.dispatchEvent({ type: "change" });
  assert.equal(errorBanner.style.display, "none", "Error banner cleared with valid cover");
  assert.equal(submitBtn.disabled, false, "Submit re-enabled");

  // 4. Test server JSON error extraction on HTTP 400
  form.dispatchEvent({
    type: "submit",
    preventDefault() {}
  });
  const xhr = getLastXHR();
  xhr.status = 400;
  xhr.responseText = JSON.stringify({ error: "Total album audio size (420.0 MB) exceeds the 350 MB limit. Please compress audio files to FLAC or MP3." });
  xhr.onload();

  assert.equal(errorBanner.style.display, "block");
  assert.equal(
    errorBanner.textContent,
    "Total album audio size (420.0 MB) exceeds the 350 MB limit. Please compress audio files to FLAC or MP3.",
    "Specific server error message is displayed to user"
  );
});

test("band_upload.js: client-side size limits immediately warn and block submission, and parses server error JSON", () => {
  const { root, sandbox, docListeners, getLastXHR } = setupMockEnvironment();

  const form = new MockElement("form");
  form.setAttribute("data-band-form", "true");
  form.setAttribute("action", "/submit-band");
  form.setAttribute("data-msg-uploading", "Uploading... {percent}% ({loaded} / {total})");
  form.setAttribute("data-msg-track-too-large", "Track '{name}' ({size}) exceeds {max}.");
  form.setAttribute("data-msg-album-too-large", "Total audio size ({size}) exceeds {max}.");
  form.setAttribute("data-msg-cover-too-large", "Cover image ({size}) exceeds {max}.");
  form.setAttribute("data-msg-photo-too-large", "Photo ({size}) exceeds {max}.");
  form.setAttribute("data-msg-server-error", "Server error ({status}).");
  form.setAttribute("data-max-track-bytes", String(50 * 1024 * 1024));
  form.setAttribute("data-max-album-bytes", String(350 * 1024 * 1024));
  form.setAttribute("data-max-cover-bytes", String(15 * 1024 * 1024));
  form.setAttribute("data-max-photo-bytes", String(25 * 1024 * 1024));

  const nameInput = new MockElement("input", "name");
  nameInput.value = "New Band";
  form.appendChild(nameInput);

  const photoInput = new MockElement("input");
  photoInput.setAttribute("data-band-photo-input", "true");
  photoInput.files = [];
  form.appendChild(photoInput);

  const photoPreview = new MockElement("img");
  photoPreview.setAttribute("data-band-photo-preview", "true");
  form.appendChild(photoPreview);

  const releaseToggle = new MockElement("input");
  releaseToggle.setAttribute("data-band-has-release-toggle", "true");
  releaseToggle.checked = false;
  form.appendChild(releaseToggle);

  const releaseSection = new MockElement("div");
  releaseSection.setAttribute("data-band-release-section", "true");
  releaseSection.style.display = "none";
  form.appendChild(releaseSection);

  const relNameInput = new MockElement("input", "release_name");
  relNameInput.value = "First Release";
  releaseSection.appendChild(relNameInput);

  const relYearInput = new MockElement("input", "release_date_published");
  relYearInput.value = "1998";
  releaseSection.appendChild(relYearInput);

  const coverInput = new MockElement("input");
  coverInput.setAttribute("data-band-cover-input", "true");
  coverInput.files = [];
  releaseSection.appendChild(coverInput);

  const coverPreview = new MockElement("div");
  coverPreview.setAttribute("data-band-cover-preview", "true");
  releaseSection.appendChild(coverPreview);

  const audioInput = new MockElement("input");
  audioInput.setAttribute("data-band-audio-input", "true");
  audioInput.files = [];
  releaseSection.appendChild(audioInput);

  const addAudioBtn = new MockElement("button");
  addAudioBtn.setAttribute("data-band-add-audio", "true");
  releaseSection.appendChild(addAudioBtn);

  const dropzone = new MockElement("div");
  dropzone.setAttribute("data-band-dropzone", "true");
  releaseSection.appendChild(dropzone);

  const trackList = new MockElement("div");
  trackList.setAttribute("data-band-track-list", "true");
  releaseSection.appendChild(trackList);

  const progressContainer = new MockElement("div");
  progressContainer.setAttribute("data-band-progress", "true");
  progressContainer.style.display = "none";
  form.appendChild(progressContainer);

  const errorBanner = new MockElement("div");
  errorBanner.setAttribute("data-band-upload-error", "true");
  errorBanner.style.display = "none";
  form.appendChild(errorBanner);

  const submitBtn = new MockElement("button");
  submitBtn.setAttribute("data-band-submit", "true");
  form.appendChild(submitBtn);

  root.appendChild(form);

  vm.runInNewContext(bandUploadJs, sandbox);
  (docListeners.get("DOMContentLoaded") || []).forEach((h) => h());

  // Band only: valid initially
  assert.equal(submitBtn.disabled, false);

  // 1. Oversized band photo (30 MB > 25 MB)
  photoInput.files = [{ name: "big_photo.jpg", size: 30 * 1024 * 1024 }];
  photoInput.dispatchEvent({ type: "change" });
  assert.equal(errorBanner.style.display, "block", "Error banner shown for oversized photo");
  assert.match(errorBanner.textContent, /Photo \(30\.0 MB\) exceeds 25\.0 MB\./);
  assert.equal(submitBtn.disabled, true, "Submit button disabled with oversized photo");

  // Fix photo
  photoInput.files = [{ name: "photo.jpg", size: 5 * 1024 * 1024 }];
  photoInput.dispatchEvent({ type: "change" });
  assert.equal(errorBanner.style.display, "none");
  assert.equal(submitBtn.disabled, false);

  // 2. Enable release section and add tracks > 350 MB
  releaseToggle.checked = true;
  releaseToggle.dispatchEvent({ type: "change" });

  const tracksBatch = [];
  for (let i = 1; i <= 10; i++) {
    tracksBatch.push({ name: `track_${i}.wav`, type: "audio/wav", size: 40 * 1024 * 1024 });
  }
  dropzone.dispatchEvent({
    type: "drop",
    preventDefault() {},
    stopPropagation() {},
    dataTransfer: { files: tracksBatch }
  });

  assert.equal(errorBanner.style.display, "block", "Error banner shown for release > 350 MB");
  assert.match(errorBanner.textContent, /Total audio size \(400\.0 MB\) exceeds 350\.0 MB\./);
  assert.equal(submitBtn.disabled, true);

  // Fix tracks to under 350 MB
  trackList.children[9].querySelector(".btn-remove").click();
  trackList.children[8].querySelector(".btn-remove").click();
  assert.equal(errorBanner.style.display, "none");
  assert.equal(submitBtn.disabled, false);

  // 3. Test server JSON error extraction
  form.dispatchEvent({
    type: "submit",
    preventDefault() {}
  });
  const xhr = getLastXHR();
  xhr.status = 400;
  xhr.responseText = JSON.stringify({ error: "Band photo rejected: not a recognized image or video file" });
  xhr.onload();

  assert.equal(errorBanner.style.display, "block");
  assert.equal(
    errorBanner.textContent,
    "Band photo rejected: not a recognized image or video file",
    "Specific server error message is displayed to user"
  );
});

