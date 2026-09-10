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

  // Keyboard navigation for the photo lightbox (see GitHub issue #29):
  // left/right arrows move to the previous/next photo, Escape closes it.
  // The lightbox itself is pure CSS (:target-based), so this only needs
  // to click the already-present prev/next/close links for whichever
  // lightbox is currently open.
  document.addEventListener("keydown", function (event) {
    var open = document.querySelector(".lightbox:target");
    if (!open) return;

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

  // Only one <audio>/<video> plays at a time site-wide - starting one
  // pauses every other, so playing a gallery video while a track (or
  // another video) is already going doesn't leave both audible at once.
  // 'play' doesn't bubble, so this has to listen on the capture phase.
  document.addEventListener(
    "play",
    function (event) {
      var target = event.target;
      if (target.tagName !== "AUDIO" && target.tagName !== "VIDEO") return;
      var players = document.querySelectorAll("audio, video");
      for (var i = 0; i < players.length; i++) {
        if (players[i] !== target) players[i].pause();
      }
    },
    true
  );
})();
