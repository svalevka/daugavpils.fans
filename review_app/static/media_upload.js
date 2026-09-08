// Drives the /submit/<band>/media upload form (see GitHub issue #21,
// revised after real-use feedback: a plain <input type="file" multiple>
// REPLACES its selection on every dialog open rather than accumulating -
// someone who picks one file, then opens the picker again for a second,
// silently loses the first with no warning. This file keeps its own
// running list instead: "Add photos or videos" can be clicked repeatedly,
// each pick is appended (never replaces), each item gets a preview
// (photos only - no thumbnail-generation pipeline for video, matching
// the site's own media pages), its own optional caption, and a remove
// control. "Submit for review" starts disabled and only enables once the
// list is non-empty, so it's never ambiguous whether there's "more" to
// add or whether the batch is done.
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var input = document.querySelector("[data-media-files]");
    var addButton = document.querySelector("[data-media-add]");
    var list = document.querySelector("[data-media-file-list]");
    var submitButton = document.querySelector("[data-media-submit]");
    var form = input ? input.closest("form") : null;
    if (!input || !addButton || !list || !submitButton || !form) return;

    // {file: File, previewUrl: string|null, caption: string} per item.
    // caption/previewUrl live here, not just in the DOM, so they survive
    // the full re-render every add/remove triggers.
    var items = [];

    function updateSubmitState() {
      submitButton.disabled = items.length === 0;
    }

    function removeItem(index) {
      var item = items[index];
      if (item.previewUrl) URL.revokeObjectURL(item.previewUrl);
      items.splice(index, 1);
      render();
    }

    function render() {
      list.innerHTML = "";
      items.forEach(function (item, index) {
        var row = document.createElement("div");
        row.className = "media-item";

        if (item.previewUrl) {
          var img = document.createElement("img");
          img.src = item.previewUrl;
          img.alt = item.file.name;
          img.className = "media-item-preview";
          row.appendChild(img);
        } else {
          var placeholder = document.createElement("div");
          placeholder.className = "media-item-preview media-item-preview-video";
          placeholder.textContent = "Video";
          row.appendChild(placeholder);
        }

        var details = document.createElement("div");
        details.className = "media-item-details";

        var name = document.createElement("div");
        name.className = "media-item-name";
        name.textContent = item.file.name;
        details.appendChild(name);

        var captionLabel = document.createElement("label");
        captionLabel.setAttribute("for", "caption_" + index);
        captionLabel.textContent = "Caption or note (optional)";
        details.appendChild(captionLabel);

        var captionInput = document.createElement("input");
        captionInput.type = "text";
        captionInput.id = "caption_" + index;
        captionInput.name = "caption_" + index;
        captionInput.value = item.caption;
        captionInput.addEventListener("input", function () {
          item.caption = captionInput.value;
        });
        details.appendChild(captionInput);

        row.appendChild(details);

        var removeButton = document.createElement("button");
        removeButton.type = "button";
        removeButton.className = "media-item-remove";
        removeButton.setAttribute("aria-label", "Remove " + item.file.name);
        removeButton.textContent = "✕";
        removeButton.addEventListener("click", function () {
          removeItem(index);
        });
        row.appendChild(removeButton);

        list.appendChild(row);
      });
      updateSubmitState();
    }

    addButton.addEventListener("click", function () {
      input.click();
    });

    input.addEventListener("change", function () {
      Array.prototype.forEach.call(input.files, function (file) {
        items.push({
          file: file,
          previewUrl: file.type.indexOf("image/") === 0 ? URL.createObjectURL(file) : null,
          caption: "",
        });
      });
      // Reset so picking the exact same file again still fires `change`,
      // and so this input never itself carries a stale FileList between
      // picks - the real submission payload is assembled from `items`
      // (see the submit handler below), not from this input's own state.
      input.value = "";
      render();
    });

    form.addEventListener("submit", function () {
      var dt = new DataTransfer();
      items.forEach(function (item) {
        dt.items.add(item.file);
      });
      input.files = dt.files;
    });

    updateSubmitState();
  });
})();
