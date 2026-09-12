// Opt-in autoplay for release tracklists: when enabled, playing one track
// to the end starts the next one. Off by default; the choice is remembered
// per-browser in localStorage. See GitHub issue #23.
(function () {
  "use strict";

  var STORAGE_KEY = "daugavpils-fans:autoplay";

  function isAutoplayOn() {
    try {
      return localStorage.getItem(STORAGE_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function setAutoplayOn(on) {
    try {
      localStorage.setItem(STORAGE_KEY, on ? "1" : "0");
    } catch (e) {
      // Storage unavailable (private browsing, blocked cookies, etc.) -
      // the toggle still works for the current page load.
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    var toggle = document.querySelector("[data-autoplay-toggle]");
    var players = Array.prototype.slice.call(
      document.querySelectorAll("table.tracklist audio")
    );
    if (!toggle || players.length < 2) {
      if (toggle) toggle.closest(".autoplay-control").hidden = true;
      return;
    }

    toggle.checked = isAutoplayOn();
    toggle.addEventListener("change", function () {
      setAutoplayOn(toggle.checked);
    });

    players.forEach(function (audio, index) {
      audio.addEventListener("ended", function () {
        if (!toggle.checked) return;
        var next = players[index + 1];
        if (next) next.play();
      });
    });
  });

  // Keyboard navigation for the photo and video lightboxes (see GitHub
  // issues #29 and #30): left/right arrows move to the previous/next
  // item, Escape closes it. The lightboxes themselves are pure CSS
  // (:target-based), so this only needs to click the already-present
  // prev/next/close links for whichever lightbox is currently open.
  document.addEventListener("keydown", function (event) {
    var open = document.querySelector(".lightbox:target");
    if (!open) return;

    // In a video lightbox, let left/right arrows seek within the video
    // when the <video> player itself has focus, rather than paging to
    // the previous/next video.
    if (
      (event.key === "ArrowLeft" || event.key === "ArrowRight") &&
      event.target &&
      event.target.tagName === "VIDEO"
    ) {
      return;
    }

    var selector = null;
    if (event.key === "ArrowLeft") selector = ".lightbox-prev";
    else if (event.key === "ArrowRight") selector = ".lightbox-next";
    else if (event.key === "Escape") selector = ".lightbox-close";
    if (!selector) return;

    var link = open.querySelector(selector);
    if (!link) return;
    event.preventDefault();
    link.click();
  });

  // Track and coordinate video playback in the lightbox (see GitHub issue #30):
  // 1. When a video lightbox is closed or navigated away from, pause its video
  //    so audio doesn't keep playing invisibly in the background (CSS :target
  //    only hides the old lightbox with display:none).
  // 2. When opening a video lightbox (by clicking its thumbnail) or navigating
  //    between videos in it, start playback automatically for a smooth viewer
  //    experience.
  // 3. When opening a video lightbox, pause any other media playing on the page.
  var activeLightboxVideo = null;

  window.addEventListener("hashchange", function () {
    var hash = location.hash.slice(1);
    var target = hash ? document.getElementById(hash) : null;
    var targetIsVideoLightbox = !!(
      target && target.classList.contains("video-lightbox")
    );
    var targetVideo = targetIsVideoLightbox
      ? target.querySelector("video")
      : null;

    if (activeLightboxVideo) {
      activeLightboxVideo.pause();
      activeLightboxVideo = null;
    }

    var allLightboxVideos = document.querySelectorAll(".video-lightbox video");
    for (var i = 0; i < allLightboxVideos.length; i++) {
      if (allLightboxVideos[i] !== targetVideo && !allLightboxVideos[i].paused) {
        allLightboxVideos[i].pause();
      }
    }

    if (targetIsVideoLightbox && targetVideo) {
      activeLightboxVideo = targetVideo;

      // Pause any audio track playing outside this lightbox
      var allMedia = document.querySelectorAll("audio, video");
      for (var j = 0; j < allMedia.length; j++) {
        var el = allMedia[j];
        if (el !== targetVideo && !el.paused) {
          el.pause();
        }
      }

      var promise = targetVideo.play();
      if (promise && promise.catch) {
        promise.catch(function () {
          // Autoplay policy prevented playback; remains paused with controls ready.
        });
      }
    }
  });

  // Only one <audio>/<video> plays at a time site-wide - starting one
  // pauses every other, so playing a gallery video while a track (or
  // another video) is already going doesn't leave both audible at once.
  // 'play' doesn't bubble, so this has to listen on the capture phase.
  document.addEventListener(
    "play",
    function (event) {
      var target = event.target;
      if (target.tagName !== "AUDIO" && target.tagName !== "VIDEO") return;
      if (target.closest && target.closest(".video-lightbox")) {
        activeLightboxVideo = target;
      }
      var players = document.querySelectorAll("audio, video");
      for (var i = 0; i < players.length; i++) {
        if (players[i] !== target) players[i].pause();
      }
    },
    true
  );
})();
