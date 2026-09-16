// Auto-save and draft recovery in localStorage for band and album submissions (GitHub issue #64).
// Automatically persists unsaved text inputs across reloads, accidental navigation, or browser crashes.
// On page load, restores saved fields, provides a clear button, and purges upon successful submission.
(function () {
  "use strict";

  function getStorageKey(form) {
    var customKey = form.getAttribute("data-draft-key");
    if (customKey) return "daugavpils-draft:" + customKey;
    var path = window.location && window.location.pathname ? window.location.pathname : "/submit";
    return "daugavpils-draft:" + path;
  }

  function isIgnoredField(el) {
    if (!el || !el.name) return true;
    var type = (el.type || "").toLowerCase();
    if (type === "hidden" || type === "file" || type === "password" || type === "submit" || type === "button") {
      return true;
    }
    if (el.name === "csrf_token" || el.name === "website") {
      return true;
    }
    return false;
  }

  function formatDraftTime(timestamp) {
    if (!timestamp) return "";
    var d = new Date(timestamp);
    if (isNaN(d.getTime())) return "";
    var now = new Date();
    var isToday = d.toDateString() === now.toDateString();
    var hours = d.getHours() < 10 ? "0" + d.getHours() : String(d.getHours());
    var mins = d.getMinutes() < 10 ? "0" + d.getMinutes() : String(d.getMinutes());
    var timeStr = hours + ":" + mins;
    if (isToday) {
      return timeStr;
    }
    var day = d.getDate() < 10 ? "0" + d.getDate() : String(d.getDate());
    var month = (d.getMonth() + 1) < 10 ? "0" + (d.getMonth() + 1) : String(d.getMonth() + 1);
    return day + "." + month + "." + d.getFullYear() + " " + timeStr;
  }

  function serializeFields(form) {
    var fields = {};
    var hasContent = false;
    var elements = form.elements;

    for (var i = 0; i < elements.length; i++) {
      var el = elements[i];
      if (isIgnoredField(el)) continue;

      var name = el.name || el.id;
      if (!name) continue;

      if (el.type === "checkbox") {
        fields[name] = el.checked;
        if (el.checked) hasContent = true;
      } else if (el.type === "radio") {
        if (el.checked) {
          fields[name] = el.value;
          hasContent = true;
        }
      } else {
        var val = el.value || "";
        fields[name] = val;
        if (val.trim().length > 0) {
          hasContent = true;
        }
      }
    }

    return hasContent ? fields : null;
  }

  function saveDraft(form) {
    try {
      var key = getStorageKey(form);
      var fields = serializeFields(form);
      if (!fields) {
        localStorage.removeItem(key);
        return;
      }
      var payload = {
        timestamp: Date.now(),
        fields: fields
      };
      localStorage.setItem(key, JSON.stringify(payload));
    } catch (e) {
      // localStorage may be disabled or quota exceeded
    }
  }

  function clearDraft(form) {
    try {
      var key = getStorageKey(form);
      localStorage.removeItem(key);
    } catch (e) {}

    var banner = form.querySelector("[data-draft-banner]");
    if (banner) {
      banner.style.display = "none";
    }
  }

  function restoreDraft(form) {
    try {
      var key = getStorageKey(form);
      var raw = localStorage.getItem(key);
      if (!raw) return false;

      var payload = JSON.parse(raw);
      if (!payload || !payload.fields) return false;

      var fields = payload.fields;
      var hasRestoredData = false;

      Object.keys(fields).forEach(function (name) {
        var el = form.elements[name] || form.querySelector('[name="' + name + '"]') || form.querySelector('#' + name);
        if (!el || isIgnoredField(el)) return;

        var val = fields[name];
        if (el.type === "checkbox") {
          el.checked = Boolean(val);
          if (el.checked) hasRestoredData = true;
          try {
            el.dispatchEvent(new Event("change", { bubbles: true }));
          } catch (e) {}
        } else if (el.type === "radio") {
          if (el.value === val) {
            el.checked = true;
            hasRestoredData = true;
            try {
              el.dispatchEvent(new Event("change", { bubbles: true }));
            } catch (e) {}
          }
        } else {
          el.value = val;
          if (String(val).trim().length > 0) hasRestoredData = true;
          try {
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
          } catch (e) {}
        }
      });

      if (!hasRestoredData) {
        localStorage.removeItem(key);
        return false;
      }

      // Show draft banner
      var banner = form.querySelector("[data-draft-banner]");
      var textEl = form.querySelector("[data-draft-text]");
      var clearBtn = form.querySelector("[data-draft-clear]");

      if (banner) {
        var msgTpl = form.getAttribute("data-msg-draft-restored") || "Restored unsaved draft from {time}.";
        var timeStr = formatDraftTime(payload.timestamp);
        var msg = msgTpl.replace("{time}", timeStr);

        if (textEl) {
          textEl.textContent = msg;
        } else {
          banner.textContent = msg;
        }
        banner.style.display = "flex";

        if (clearBtn) {
          clearBtn.style.display = "inline-block";
          clearBtn.onclick = function (e) {
            e.preventDefault();
            e.stopPropagation();

            // Clear stored draft
            try {
              localStorage.removeItem(key);
            } catch (err) {}

            // Reset form fields (excluding hidden/csrf)
            var elements = form.elements;
            for (var i = 0; i < elements.length; i++) {
              var f = elements[i];
              if (isIgnoredField(f)) continue;
              if (f.type === "checkbox" || f.type === "radio") {
                f.checked = false;
                try {
                  f.dispatchEvent(new Event("change", { bubbles: true }));
                } catch (err) {}
              } else {
                f.value = "";
                try {
                  f.dispatchEvent(new Event("input", { bubbles: true }));
                  f.dispatchEvent(new Event("change", { bubbles: true }));
                } catch (err) {}
              }
            }

            var clearedMsg = form.getAttribute("data-msg-draft-cleared") || "Draft cleared.";
            if (textEl) {
              textEl.textContent = clearedMsg;
            } else {
              banner.textContent = clearedMsg;
            }
            clearBtn.style.display = "none";

            setTimeout(function () {
              banner.style.display = "none";
            }, 1500);
          };
        }
      }

      return true;
    } catch (e) {
      return false;
    }
  }

  function initForm(form) {
    var saveTimer = null;
    function scheduleSave() {
      if (saveTimer) clearTimeout(saveTimer);
      saveTimer = setTimeout(function () {
        saveDraft(form);
      }, 400);
    }

    form.addEventListener("input", function (e) {
      if (isIgnoredField(e.target)) return;
      scheduleSave();
    });

    form.addEventListener("change", function (e) {
      if (isIgnoredField(e.target)) return;
      scheduleSave();
    });

    // Custom event to clear draft
    form.addEventListener("draft:clear", function () {
      clearDraft(form);
    });

    // Restore on init
    restoreDraft(form);
  }

  function init() {
    var forms = document.querySelectorAll("form[data-draft-form], form[data-band-form], form[data-album-form]");
    for (var i = 0; i < forms.length; i++) {
      initForm(forms[i]);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Exported API
  window.DraftRecovery = {
    save: saveDraft,
    restore: restoreDraft,
    clear: clearDraft,
    formatDraftTime: formatDraftTime
  };
})();
