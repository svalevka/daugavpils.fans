// Drives the /submit/<band>/add-release upload form (GitHub issue #20).
// Allows selecting multiple audio files, reordering tracks (Up/Down),
// editing track titles, selecting an optional cover image with preview,
// and assembling a clean FormData submission.
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var audioInput = document.querySelector("[data-album-audio-input]");
    var addAudioBtn = document.querySelector("[data-album-add-audio]");
    var trackList = document.querySelector("[data-album-track-list]");
    var coverInput = document.querySelector("[data-album-cover-input]");
    var coverPreview = document.querySelector("[data-album-cover-preview]");
    var submitButton = document.querySelector("[data-album-submit]");
    var form = document.querySelector("[data-album-form]");

    if (!audioInput || !addAudioBtn || !trackList || !submitButton || !form) return;

    // Array of {file: File, title: string}
    var tracks = [];

    function cleanTrackTitle(filename) {
      var stem = filename.replace(/\.[^/.]+$/, "");
      return stem.replace(/^\d+[\s._-]+/, "").trim() || stem;
    }

    function updateSubmitState() {
      var nameInput = form.querySelector("#name");
      var yearInput = form.querySelector("#date_published");
      var hasTitle = nameInput && nameInput.value.trim().length > 0;
      var hasYear = yearInput && /^\d{4}$/.test(yearInput.value.trim());
      submitButton.disabled = !(tracks.length > 0 && hasTitle && hasYear);
    }

    function renderTracks() {
      trackList.innerHTML = "";
      tracks.forEach(function (track, index) {
        var row = document.createElement("div");
        row.className = "track-row";
        row.style.cssText = "display: flex; align-items: center; gap: 8px; margin-bottom: 8px; background: rgba(0,0,0,0.03); padding: 6px 10px; border-radius: 4px;";

        var posSpan = document.createElement("span");
        posSpan.className = "track-pos";
        posSpan.style.cssText = "font-weight: bold; min-width: 24px;";
        posSpan.textContent = (index + 1) + ".";
        row.appendChild(posSpan);

        var titleInput = document.createElement("input");
        titleInput.type = "text";
        titleInput.value = track.title;
        titleInput.className = "track-title-input";
        titleInput.style.cssText = "flex: 1; padding: 4px 8px;";
        titleInput.placeholder = "Track title";
        titleInput.addEventListener("input", function () {
          track.title = titleInput.value;
        });
        row.appendChild(titleInput);

        var fileSpan = document.createElement("span");
        fileSpan.className = "track-filename meta";
        fileSpan.style.cssText = "font-size: 0.85em; opacity: 0.7; max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;";
        fileSpan.textContent = track.file.name;
        row.appendChild(fileSpan);

        // Move Up
        if (index > 0) {
          var upBtn = document.createElement("button");
          upBtn.type = "button";
          upBtn.className = "btn-icon";
          upBtn.textContent = "▲";
          upBtn.title = "Move Up";
          upBtn.addEventListener("click", function () {
            var temp = tracks[index - 1];
            tracks[index - 1] = tracks[index];
            tracks[index] = temp;
            renderTracks();
          });
          row.appendChild(upBtn);
        }

        // Move Down
        if (index < tracks.length - 1) {
          var downBtn = document.createElement("button");
          downBtn.type = "button";
          downBtn.className = "btn-icon";
          downBtn.textContent = "▼";
          downBtn.title = "Move Down";
          downBtn.addEventListener("click", function () {
            var temp = tracks[index + 1];
            tracks[index + 1] = tracks[index];
            tracks[index] = temp;
            renderTracks();
          });
          row.appendChild(downBtn);
        }

        // Remove
        var removeBtn = document.createElement("button");
        removeBtn.type = "button";
        removeBtn.className = "btn-icon btn-remove";
        removeBtn.textContent = "✕";
        removeBtn.title = "Remove";
        removeBtn.addEventListener("click", function () {
          tracks.splice(index, 1);
          renderTracks();
          updateSubmitState();
        });
        row.appendChild(removeBtn);

        trackList.appendChild(row);
      });

      updateSubmitState();
    }

    addAudioBtn.addEventListener("click", function () {
      audioInput.click();
    });

    audioInput.addEventListener("change", function () {
      if (!audioInput.files) return;
      Array.from(audioInput.files).forEach(function (file) {
        tracks.push({
          file: file,
          title: cleanTrackTitle(file.name)
        });
      });
      audioInput.value = "";
      renderTracks();
    });

    if (coverInput && coverPreview) {
      coverInput.addEventListener("change", function () {
        coverPreview.innerHTML = "";
        if (coverInput.files && coverInput.files[0]) {
          var file = coverInput.files[0];
          var img = document.createElement("img");
          img.src = URL.createObjectURL(file);
          img.style.cssText = "max-width: 160px; max-height: 160px; border-radius: 4px; object-fit: cover; margin-top: 6px;";
          coverPreview.appendChild(img);
        }
      });
    }

    var nameInput = form.querySelector("#name");
    var yearInput = form.querySelector("#date_published");
    if (nameInput) nameInput.addEventListener("input", updateSubmitState);
    if (yearInput) yearInput.addEventListener("input", updateSubmitState);

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (tracks.length === 0) return;

      var formData = new FormData(form);
      formData.delete("tracks");

      tracks.forEach(function (track, index) {
        formData.append("tracks", track.file);
        formData.append("track_title_" + index, track.title);
      });

      submitButton.disabled = true;
      submitButton.textContent = submitButton.getAttribute("data-loading-text") || "Uploading...";

      fetch(form.action, {
        method: "POST",
        body: formData
      }).then(function (response) {
        if (response.ok) {
          return response.text().then(function (html) {
            document.open();
            document.write(html);
            document.close();
          });
        } else if (response.status === 429) {
          alert("Too many submissions. Please wait an hour before submitting again.");
          submitButton.disabled = false;
          submitButton.textContent = "Submit for review";
        } else {
          alert("Submission failed (error " + response.status + "). Please check your files and details.");
          submitButton.disabled = false;
          submitButton.textContent = "Submit for review";
        }
      }).catch(function (err) {
        alert("Network error: " + err.message);
        submitButton.disabled = false;
        submitButton.textContent = "Submit for review";
      });
    });
  });
})();
