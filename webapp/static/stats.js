// Privacy-preserving client-side analytics beacon (GitHub issue #34).
// Sends pageviews on load, and tracks audio/video playback after >= 5 seconds of active playback.
(function () {
  "use strict";

  function sendEvent(data) {
    try {
      var payload = JSON.stringify(data);
      if (navigator.sendBeacon) {
        var blob = new Blob([payload], { type: "application/json" });
        navigator.sendBeacon("/api/event", blob);
      } else {
        fetch("/api/event", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: payload,
          keepalive: true,
        }).catch(function () {});
      }
    } catch (e) {
      // Analytics must never break the user experience
    }
  }

  // 1. Pageview beacon
  sendEvent({
    type: "pageview",
    path: window.location.pathname,
    referrer: document.referrer || "",
  });

  // 2. Media playback tracking (audio & video >= 5 seconds)
  var reportedMedia = new Set();
  var mediaPlayState = new WeakMap();

  document.addEventListener(
    "play",
    function (event) {
      var target = event.target;
      if (target.tagName !== "AUDIO" && target.tagName !== "VIDEO") return;
      if (!mediaPlayState.has(target)) {
        mediaPlayState.set(target, { playedSeconds: 0, lastTime: Date.now() });
      } else {
        var state = mediaPlayState.get(target);
        state.lastTime = Date.now();
      }
    },
    true
  );

  document.addEventListener(
    "timeupdate",
    function (event) {
      var target = event.target;
      if (target.tagName !== "AUDIO" && target.tagName !== "VIDEO") return;
      var state = mediaPlayState.get(target);
      if (!state) return;

      var now = Date.now();
      var delta = (now - state.lastTime) / 1000;
      state.lastTime = now;

      // Ignore seeking or background tab throttling jumps (> 2s)
      if (delta > 0 && delta < 2) {
        state.playedSeconds += delta;
      }

      var mediaKey =
        target.src ||
        (target.querySelector("source") ? target.querySelector("source").src : "") ||
        target.dataset.track ||
        target.dataset.video ||
        "";

      if (state.playedSeconds >= 5 && mediaKey && !reportedMedia.has(mediaKey)) {
        reportedMedia.add(mediaKey);
        var isAudio = target.tagName === "AUDIO";
        sendEvent({
          type: isAudio ? "track_play" : "video_play",
          path: window.location.pathname,
          track: target.dataset.track || "",
          video: target.dataset.video || "",
          band: target.dataset.band || "",
          release: target.dataset.release || "",
        });
      }
    },
    true
  );

  document.addEventListener(
    "pause",
    function (event) {
      var target = event.target;
      if (mediaPlayState.has(target)) {
        mediaPlayState.get(target).lastTime = Date.now();
      }
    },
    true
  );
})();
