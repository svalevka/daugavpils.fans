// Renders one optional caption field per selected file on the
// /submit/<band>/media upload form (see GitHub issue #21) - a single
// shared note wouldn't make sense for a batch of unrelated photos, so
// each file gets its own caption_<index> input, matching the order
// request.files.getlist("files") sees server-side.
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var input = document.querySelector("[data-media-files]");
    var list = document.querySelector("[data-media-caption-list]");
    if (!input || !list) return;

    input.addEventListener("change", function () {
      list.innerHTML = "";
      Array.prototype.forEach.call(input.files, function (file, index) {
        var row = document.createElement("div");
        row.className = "field";

        var label = document.createElement("label");
        label.setAttribute("for", "caption_" + index);
        label.textContent = file.name + " — caption or note (optional)";

        var textInput = document.createElement("input");
        textInput.type = "text";
        textInput.id = "caption_" + index;
        textInput.name = "caption_" + index;

        row.appendChild(label);
        row.appendChild(textInput);
        list.appendChild(row);
      });
    });
  });
})();
