// Drives the /submit/add-band upload form (GitHub issue #22).
// Handles optional band photo preview, optional first release toggle,
// multi-track audio selection, track reordering, and FormData submission.
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var form = document.querySelector("[data-band-form]");
    if (!form) return;

    var nameInput = form.querySelector("#name");
    var photoInput = form.querySelector("[data-band-photo-input]");
    var photoPreview = form.querySelector("[data-band-photo-preview]");
    var releaseToggle = form.querySelector("[data-band-has-release-toggle]");
    var releaseSection = form.querySelector("[data-band-release-section]");
    var submitButton = form.querySelector("[data-band-submit]");

    // Release fields
    var relNameInput = form.querySelector("#release_name");
    var relYearInput = form.querySelector("#release_date_published");
    var audioInput = form.querySelector("[data-band-audio-input]");
    var addAudioBtn = form.querySelector("[data-band-add-audio]");
    var trackList = form.querySelector("[data-band-track-list]");
    var coverInput = form.querySelector("[data-band-cover-input]");
    var coverPreview = form.querySelector("[data-band-cover-preview]");

    // Array of {file: File, title: string}
    var tracks = [];

    function cleanTrackTitle(filename) {
      var stem = filename.replace(/\.[^/.]+$/, "");
      return stem.replace(/^\d+[\s._-]+/, "").trim() || stem;
    }

    function updateSubmitState() {
      var hasBandName = nameInput && nameInput.value.trim().length > 0;
      var hasRelease = releaseToggle && releaseToggle.checked;

      if (!hasBandName) {
        submitButton.disabled = true;
        return;
      }

      if (hasRelease) {
        var hasRelName = relNameInput && relNameInput.value.trim().length > 0;
        var hasRelYear = relYearInput && /^\d{4}$/.test(relYearInput.value.trim());
        var hasTracks = tracks.length > 0;
        submitButton.disabled = !(hasRelName && hasRelYear && hasTracks);
      } else {
        submitButton.disabled = false;
      }
    }

    if (nameInput) {
      nameInput.addEventListener("input", updateSubmitState);
    }
    if (relNameInput) {
      relNameInput.addEventListener("input", updateSubmitState);
    }
    if (relYearInput) {
      relYearInput.addEventListener("input", updateSubmitState);
    }

    // Toggle release section
    if (releaseToggle && releaseSection) {
      releaseToggle.addEventListener("change", function () {
        if (releaseToggle.checked) {
          releaseSection.style.display = "block";
        } else {
          releaseSection.style.display = "none";
        }
        updateSubmitState();
      });
    }

    // Band photo preview
    if (photoInput && photoPreview) {
      photoInput.addEventListener("change", function () {
        var file = photoInput.files[0];
        if (file) {
          var reader = new FileReader();
          reader.onload = function (e) {
            photoPreview.src = e.target.result;
            photoPreview.style.display = "block";
          };
          reader.readAsDataURL(file);
        } else {
          photoPreview.style.display = "none";
        }
      });
    }

    // Release cover preview
    if (coverInput && coverPreview) {
      coverInput.addEventListener("change", function () {
        var file = coverInput.files[0];
        if (file) {
          var reader = new FileReader();
          reader.onload = function (e) {
            coverPreview.src = e.target.result;
            coverPreview.style.display = "block";
          };
          reader.readAsDataURL(file);
        } else {
          coverPreview.style.display = "none";
        }
      });
    }

    // Tracks management
    function renderTracks() {
      if (!trackList) return;
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
        removeBtn.style.color = "#c00";
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

    if (addAudioBtn && audioInput) {
      addAudioBtn.addEventListener("click", function () {
        audioInput.click();
      });

      audioInput.addEventListener("change", function () {
        var files = Array.from(audioInput.files);
        files.forEach(function (f) {
          tracks.push({
            file: f,
            title: cleanTrackTitle(f.name),
          });
        });
        audioInput.value = "";
        renderTracks();
      });
    }

    // Custom form submission with ordered tracks
    form.addEventListener("submit", function (e) {
      if (releaseToggle && releaseToggle.checked && tracks.length === 0) {
        e.preventDefault();
        alert("Please add at least one audio track to the release.");
        return;
      }

      if (releaseToggle && releaseToggle.checked) {
        e.preventDefault();
        submitButton.disabled = true;
        submitButton.textContent = "Uploading...";

        var formData = new FormData(form);
        formData.delete("tracks");

        tracks.forEach(function (t, idx) {
          formData.append("tracks", t.file, t.file.name);
          formData.append("track_title_" + idx, t.title);
        });

        var xhr = new XMLHttpRequest();
        xhr.open("POST", form.action || "/submit-band");
        xhr.onload = function () {
          if (xhr.status >= 200 && xhr.status < 400) {
            document.open();
            document.write(xhr.responseText);
            document.close();
          } else {
            alert("Submission failed (status " + xhr.status + "). Please check your files.");
            submitButton.disabled = false;
            submitButton.textContent = "Submit";
          }
        };
        xhr.onerror = function () {
          alert("Network error while submitting.");
          submitButton.disabled = false;
          submitButton.textContent = "Submit";
        };
        xhr.send(formData);
      }
    });

    updateSubmitState();
  });
})();
