// Drives the /submit/<band>/add-release upload form (GitHub issue #20, #63).
// Allows selecting multiple audio files via file picker or drag-and-drop,
// client-side audio preview before submission, track reordering (drag-and-drop & buttons),
// editing track titles, selecting an optional cover image with preview,
// and real-time upload progress reporting with network interruption recovery.
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var audioInput = document.querySelector("[data-album-audio-input]");
    var addAudioBtn = document.querySelector("[data-album-add-audio]");
    var dropzone = document.querySelector("[data-album-dropzone]");
    var trackList = document.querySelector("[data-album-track-list]");
    var coverInput = document.querySelector("[data-album-cover-input]");
    var coverPreview = document.querySelector("[data-album-cover-preview]");
    var submitButton = document.querySelector("[data-album-submit]");
    var form = document.querySelector("[data-album-form]");
    var progressContainer = document.querySelector("[data-album-progress]");
    var progressBar = document.querySelector("[data-album-progress-bar]");
    var progressStatus = document.querySelector("[data-album-progress-status]");
    var errorBanner = document.querySelector("[data-album-upload-error]");

    if (!audioInput || !addAudioBtn || !trackList || !submitButton || !form) return;

    // Messages from form data attributes with sensible fallbacks
    var msgUploading = form.getAttribute("data-msg-uploading") || "Uploading... {percent}% ({loaded} / {total})";
    var msgProcessing = form.getAttribute("data-msg-processing") || "Processing files on server...";
    var msgNetworkError = form.getAttribute("data-msg-network-error") || "Network error during upload. Please check your internet connection and try again.";
    var msgRateLimit = form.getAttribute("data-msg-rate-limit") || "Too many submissions. Please wait an hour before submitting again.";
    var msgServerError = form.getAttribute("data-msg-server-error") || "Server error ({status}). Please check your files and try again.";
    var msgPreview = form.getAttribute("data-msg-preview") || "Preview track";
    var msgPause = form.getAttribute("data-msg-pause") || "Pause";
    var origSubmitText = submitButton.getAttribute("data-original-text") || submitButton.textContent || "Submit for review";

    // Array of {file: File, title: string}
    var tracks = [];

    // Audio preview state
    var previewAudio = new Audio();
    var currentTrack = null;
    var currentTrackUrl = null;
    var currentPreviewBtn = null;

    function stopPreview() {
      if (previewAudio) {
        previewAudio.pause();
        previewAudio.removeAttribute("src");
        previewAudio.load();
      }
      if (currentTrackUrl) {
        URL.revokeObjectURL(currentTrackUrl);
        currentTrackUrl = null;
      }
      if (currentPreviewBtn) {
        currentPreviewBtn.textContent = "▶";
        currentPreviewBtn.classList.remove("playing");
        currentPreviewBtn.title = msgPreview;
        currentPreviewBtn.setAttribute("aria-label", msgPreview);
        currentPreviewBtn = null;
      }
      currentTrack = null;
    }

    previewAudio.addEventListener("ended", function () {
      stopPreview();
    });

    previewAudio.addEventListener("error", function () {
      stopPreview();
    });

    function cleanTrackTitle(filename) {
      var stem = filename.replace(/\.[^/.]+$/, "");
      return stem.replace(/^\d+[\s._-]+/, "").trim() || stem;
    }

    function isAudioFile(file) {
      if (!file) return false;
      if (file.type && file.type.indexOf("audio/") === 0) return true;
      return /\.(mp3|flac|wav|ogg)$/i.test(file.name);
    }

    function formatBytes(bytes) {
      if (!bytes || bytes <= 0) return "0 B";
      if (bytes < 1024) return bytes + " B";
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
      return (bytes / (1024 * 1024)).toFixed(1) + " MB";
    }

    function updateSubmitState() {
      var nameInput = form.querySelector("#name");
      var yearInput = form.querySelector("#date_published");
      var hasTitle = nameInput && nameInput.value.trim().length > 0;
      var hasYear = yearInput && /^\d{4}$/.test(yearInput.value.trim());
      submitButton.disabled = !(tracks.length > 0 && hasTitle && hasYear);
    }

    // Drag and drop state for track list reordering
    var dragSrcIndex = null;

    function renderTracks() {
      trackList.innerHTML = "";
      currentPreviewBtn = null;

      tracks.forEach(function (track, index) {
        var row = document.createElement("div");
        row.className = "track-row";
        row.style.cssText = "display: flex; align-items: center; gap: 8px; margin-bottom: 8px; background: rgba(0,0,0,0.03); padding: 6px 10px; border-radius: 4px;";
        row.setAttribute("draggable", "true");

        // Drag events for row reordering
        row.addEventListener("dragstart", function (e) {
          dragSrcIndex = index;
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", String(index));
          row.classList.add("dragging");
        });

        row.addEventListener("dragend", function () {
          row.classList.remove("dragging");
          var allRows = trackList.querySelectorAll(".track-row");
          for (var i = 0; i < allRows.length; i++) {
            allRows[i].classList.remove("drag-over");
          }
          dragSrcIndex = null;
        });

        row.addEventListener("dragover", function (e) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "move";
          row.classList.add("drag-over");
        });

        row.addEventListener("dragleave", function () {
          row.classList.remove("drag-over");
        });

        row.addEventListener("drop", function (e) {
          row.classList.remove("drag-over");
          if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            // Dragged files from desktop handled by dropzone
            return;
          }
          e.preventDefault();
          if (dragSrcIndex !== null && dragSrcIndex !== undefined && dragSrcIndex !== index) {
            var moved = tracks.splice(dragSrcIndex, 1)[0];
            tracks.splice(index, 0, moved);
            renderTracks();
          }
        });

        var posSpan = document.createElement("span");
        posSpan.className = "track-pos";
        posSpan.style.cssText = "font-weight: bold; min-width: 24px;";
        posSpan.textContent = (index + 1) + ".";
        row.appendChild(posSpan);

        // Preview button
        var previewBtn = document.createElement("button");
        previewBtn.type = "button";
        previewBtn.className = "btn-preview";

        var isCurrentlyPlaying = currentTrack === track && previewAudio && !previewAudio.paused;
        if (isCurrentlyPlaying) {
          previewBtn.textContent = "⏸";
          previewBtn.classList.add("playing");
          previewBtn.title = msgPause;
          previewBtn.setAttribute("aria-label", msgPause);
          currentPreviewBtn = previewBtn;
        } else {
          previewBtn.textContent = "▶";
          previewBtn.title = msgPreview;
          previewBtn.setAttribute("aria-label", msgPreview);
        }

        previewBtn.addEventListener("click", function () {
          if (currentTrack === track) {
            if (previewAudio.paused) {
              previewBtn.textContent = "⏸";
              previewBtn.classList.add("playing");
              previewBtn.title = msgPause;
              previewBtn.setAttribute("aria-label", msgPause);
              currentPreviewBtn = previewBtn;
              var p1 = previewAudio.play();
              if (p1 && p1.catch) {
                p1.catch(function () {
                  previewBtn.textContent = "▶";
                  previewBtn.classList.remove("playing");
                  previewBtn.title = msgPreview;
                  previewBtn.setAttribute("aria-label", msgPreview);
                  if (currentPreviewBtn === previewBtn) currentPreviewBtn = null;
                });
              }
            } else {
              previewAudio.pause();
              previewBtn.textContent = "▶";
              previewBtn.classList.remove("playing");
              previewBtn.title = msgPreview;
              previewBtn.setAttribute("aria-label", msgPreview);
              currentPreviewBtn = null;
            }
          } else {
            stopPreview();
            currentTrack = track;
            currentTrackUrl = URL.createObjectURL(track.file);
            previewAudio.src = currentTrackUrl;
            previewBtn.textContent = "⏸";
            previewBtn.classList.add("playing");
            previewBtn.title = msgPause;
            previewBtn.setAttribute("aria-label", msgPause);
            currentPreviewBtn = previewBtn;
            var p2 = previewAudio.play();
            if (p2 && p2.catch) {
              p2.catch(function () {
                previewBtn.textContent = "▶";
                previewBtn.classList.remove("playing");
                previewBtn.title = msgPreview;
                previewBtn.setAttribute("aria-label", msgPreview);
                if (currentPreviewBtn === previewBtn) currentPreviewBtn = null;
              });
            }
          }
        });
        row.appendChild(previewBtn);

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
          if (currentTrack === track) {
            stopPreview();
          }
          tracks.splice(index, 1);
          renderTracks();
          updateSubmitState();
        });
        row.appendChild(removeBtn);

        trackList.appendChild(row);
      });

      updateSubmitState();
    }

    function addFiles(files) {
      if (!files || files.length === 0) return;
      var added = false;
      Array.from(files).forEach(function (file) {
        if (isAudioFile(file)) {
          tracks.push({
            file: file,
            title: cleanTrackTitle(file.name)
          });
          added = true;
        }
      });
      if (added) {
        renderTracks();
      }
    }

    addAudioBtn.addEventListener("click", function () {
      audioInput.click();
    });

    audioInput.addEventListener("change", function () {
      if (!audioInput.files) return;
      addFiles(audioInput.files);
      audioInput.value = "";
    });

    // Dropzone support
    if (dropzone) {
      ["dragenter", "dragover"].forEach(function (evName) {
        dropzone.addEventListener(evName, function (e) {
          e.preventDefault();
          e.stopPropagation();
          dropzone.classList.add("dragover");
        });
      });

      ["dragleave", "drop"].forEach(function (evName) {
        dropzone.addEventListener(evName, function (e) {
          e.preventDefault();
          e.stopPropagation();
          dropzone.classList.remove("dragover");
        });
      });

      dropzone.addEventListener("drop", function (e) {
        if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
          addFiles(e.dataTransfer.files);
        }
      });
    }

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

    function showError(message) {
      if (progressContainer) progressContainer.style.display = "none";
      if (errorBanner) {
        errorBanner.textContent = message;
        errorBanner.style.display = "block";
      } else {
        alert(message);
      }
      submitButton.disabled = false;
      submitButton.textContent = origSubmitText;
      updateSubmitState();
    }

    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (tracks.length === 0) return;

      stopPreview();

      var formData = new FormData(form);
      formData.delete("tracks");

      tracks.forEach(function (track, index) {
        formData.append("tracks", track.file, track.file.name);
        formData.append("track_title_" + index, track.title);
      });

      if (errorBanner) {
        errorBanner.style.display = "none";
        errorBanner.textContent = "";
      }
      if (progressContainer) {
        progressContainer.style.display = "block";
      }
      if (progressBar) {
        progressBar.value = 0;
      }
      if (progressStatus) {
        progressStatus.textContent = msgUploading.replace("{percent}", "0").replace("{loaded}", "0 B").replace("{total}", "...");
      }

      submitButton.disabled = true;
      submitButton.textContent = "Uploading...";

      var xhr = new XMLHttpRequest();
      xhr.open("POST", form.action || "/submit-release");

      if (xhr.upload) {
        xhr.upload.addEventListener("progress", function (e) {
          if (e.lengthComputable && e.total > 0) {
            var pct = Math.round((e.loaded / e.total) * 100);
            if (progressBar) progressBar.value = pct;
            if (progressStatus) {
              var text = msgUploading
                .replace("{percent}", pct)
                .replace("{loaded}", formatBytes(e.loaded))
                .replace("{total}", formatBytes(e.total));
              progressStatus.textContent = text;
            }
          }
        });

        xhr.upload.addEventListener("load", function () {
          if (progressBar) progressBar.value = 100;
          if (progressStatus) {
            progressStatus.textContent = msgProcessing;
          }
        });
      }

      xhr.onload = function () {
        if (xhr.status >= 200 && xhr.status < 400) {
          if (window.DraftRecovery) {
            window.DraftRecovery.clear(form);
          }
          document.open();
          document.write(xhr.responseText);
          document.close();
        } else if (xhr.status === 429) {
          showError(msgRateLimit);
        } else {
          showError(msgServerError.replace("{status}", xhr.status));
        }
      };

      xhr.onerror = function () {
        showError(msgNetworkError);
      };

      xhr.ontimeout = function () {
        showError(msgNetworkError);
      };

      xhr.send(formData);
    });

    updateSubmitState();
  });
})();
