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
})();
