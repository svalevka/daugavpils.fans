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

  // Internet Archive outage detection & notification banner.
  // When archive.org suffers downtime or CDN failure, audio/video playback
  // silently fails. We intercept media error events, verify archive.org
  // status, present a top notification banner with bilingual copy and a
  // "Retry" button, flag the failed track/video inline, cache outage status
  // in sessionStorage for 5 minutes, and dispatch a telemetry event.
  var STORAGE_OUTAGE_KEY = "daugavpils-fans:archive-outage";
  var STORAGE_DISMISSED_KEY = "daugavpils-fans:archive-outage-dismissed";
  var OUTAGE_TTL_MS = 5 * 60 * 1000; // 5 minutes

  var I18N = {
    ru: {
      title: "Сервера Internet Archive (archive.org) временно недоступны",
      desc: "Воспроизведение аудио и видео, а также загрузка изображений сейчас могут не работать из-за временного сбоя на стороне архива. Ваш компьютер и браузер в порядке.",
      retry: "Попробовать снова",
      checking: "Проверка связи...",
      stillDown: "Сервера всё ещё недоступны",
      close: "Закрыть",
      trackError: "сбой archive.org",
    },
    en: {
      title: "Internet Archive (archive.org) servers are temporarily unavailable",
      desc: "Audio and video playback, as well as images, may not work right now due to a temporary outage on archive.org. Your computer and browser are working properly.",
      retry: "Retry",
      checking: "Checking...",
      stillDown: "Servers are still unavailable",
      close: "Close",
      trackError: "archive.org error",
    },
  };

  function getLang() {
    var lang = (document.documentElement.lang || "ru").toLowerCase();
    return lang === "en" ? "en" : "ru";
  }

  function isOutageCached() {
    try {
      var raw = sessionStorage.getItem(STORAGE_OUTAGE_KEY);
      if (!raw) return false;
      var data = JSON.parse(raw);
      if (data && data.down && Date.now() - data.timestamp < OUTAGE_TTL_MS) {
        return true;
      }
      sessionStorage.removeItem(STORAGE_OUTAGE_KEY);
      return false;
    } catch (e) {
      return false;
    }
  }

  function setOutageCached(isDown) {
    try {
      if (isDown) {
        sessionStorage.setItem(
          STORAGE_OUTAGE_KEY,
          JSON.stringify({ down: true, timestamp: Date.now() })
        );
      } else {
        sessionStorage.removeItem(STORAGE_OUTAGE_KEY);
        sessionStorage.removeItem(STORAGE_DISMISSED_KEY);
      }
    } catch (e) {}
  }

  function isOutageDismissed() {
    try {
      return sessionStorage.getItem(STORAGE_DISMISSED_KEY) === "1";
    } catch (e) {
      return false;
    }
  }

  function setOutageDismissed() {
    try {
      sessionStorage.setItem(STORAGE_DISMISSED_KEY, "1");
    } catch (e) {}
  }

  function probeArchiveOrg(callback) {
    var probeUrl = "https://archive.org/services/img/internetarchive";
    var timedOut = false;
    var controller =
      typeof AbortController !== "undefined" ? new AbortController() : null;
    var timer = setTimeout(function () {
      timedOut = true;
      if (controller) {
        try {
          controller.abort();
        } catch (e) {}
      }
      callback(false);
    }, 3000);

    if (window.fetch) {
      fetch(probeUrl + "?t=" + Date.now(), {
        method: "GET",
        mode: "no-cors",
        cache: "no-store",
        signal: controller ? controller.signal : undefined,
      })
        .then(function () {
          if (!timedOut) {
            clearTimeout(timer);
            callback(true);
          }
        })
        .catch(function () {
          if (!timedOut) {
            clearTimeout(timer);
            callback(false);
          }
        });
    } else {
      var img = new Image();
      img.onload = function () {
        if (!timedOut) {
          clearTimeout(timer);
          callback(true);
        }
      };
      img.onerror = function () {
        if (!timedOut) {
          clearTimeout(timer);
          callback(false);
        }
      };
      img.src = probeUrl + "?t=" + Date.now();
    }
  }

  function showOutageBanner() {
    var existing = document.getElementById("archive-outage-banner");
    if (existing) {
      existing.style.display = "";
      return existing;
    }

    var lang = getLang();
    var t = I18N[lang];

    var banner = document.createElement("aside");
    banner.id = "archive-outage-banner";
    banner.className = "archive-outage-banner";
    banner.setAttribute("role", "alert");
    banner.setAttribute("aria-live", "assertive");

    var content = document.createElement("div");
    content.className = "archive-outage-content";

    var icon = document.createElement("span");
    icon.className = "archive-outage-icon";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = "⚠️";

    var textWrap = document.createElement("div");
    textWrap.className = "archive-outage-text";

    var title = document.createElement("strong");
    title.textContent = t.title + ". ";

    var desc = document.createElement("span");
    desc.textContent = t.desc;

    textWrap.appendChild(title);
    textWrap.appendChild(desc);
    content.appendChild(icon);
    content.appendChild(textWrap);

    var actions = document.createElement("div");
    actions.className = "archive-outage-actions";

    var retryBtn = document.createElement("button");
    retryBtn.type = "button";
    retryBtn.className = "outage-retry-btn";
    retryBtn.textContent = t.retry;
    retryBtn.addEventListener("click", function () {
      retryArchiveConnection(retryBtn, banner);
    });

    var dismissBtn = document.createElement("button");
    dismissBtn.type = "button";
    dismissBtn.className = "outage-dismiss-btn";
    dismissBtn.setAttribute("aria-label", t.close);
    dismissBtn.textContent = "✕";
    dismissBtn.addEventListener("click", function () {
      banner.style.display = "none";
      setOutageDismissed();
    });

    actions.appendChild(retryBtn);
    actions.appendChild(dismissBtn);

    banner.appendChild(content);
    banner.appendChild(actions);

    if (document.body) {
      document.body.insertBefore(banner, document.body.firstChild);
    }
    return banner;
  }

  function retryArchiveConnection(btn, banner) {
    var lang = getLang();
    var t = I18N[lang];
    btn.disabled = true;
    btn.textContent = t.checking;

    probeArchiveOrg(function (isUp) {
      if (isUp) {
        setOutageCached(false);
        if (banner) banner.remove();

        var notices = document.querySelectorAll(".media-outage-notice");
        for (var i = 0; i < notices.length; i++) {
          notices[i].remove();
        }
        var outageRows = document.querySelectorAll(
          ".media-outage-row, .media-outage-state"
        );
        for (var j = 0; j < outageRows.length; j++) {
          outageRows[j].classList.remove("media-outage-row");
          outageRows[j].classList.remove("media-outage-state");
        }

        var players = document.querySelectorAll("audio, video");
        for (var k = 0; k < players.length; k++) {
          players[k].load();
        }

        var images = document.querySelectorAll("img");
        for (var m = 0; m < images.length; m++) {
          if (images[m].src && images[m].src.indexOf("archive.org") !== -1) {
            var origSrc = images[m].src;
            images[m].src = "";
            images[m].src = origSrc;
          }
        }
      } else {
        btn.textContent = t.stillDown;
        if (banner) {
          banner.classList.remove("archive-outage-shake");
          void banner.offsetWidth;
          banner.classList.add("archive-outage-shake");
        }
        setTimeout(function () {
          btn.disabled = false;
          btn.textContent = t.retry;
        }, 1500);
      }
    });
  }

  function handleMediaError(target) {
    var mediaEl = target.tagName === "SOURCE" ? target.parentElement : target;
    if (
      !mediaEl ||
      (mediaEl.tagName !== "AUDIO" &&
        mediaEl.tagName !== "VIDEO" &&
        mediaEl.tagName !== "IMG")
    ) {
      return;
    }

    var src = mediaEl.currentSrc || mediaEl.src || "";
    if (!src && mediaEl.querySelector("source")) {
      src = mediaEl.querySelector("source").src || "";
    }
    if (src.indexOf("archive.org") === -1) {
      return;
    }

    var lang = getLang();
    var t = I18N[lang];

    if (mediaEl.tagName === "AUDIO") {
      var row = mediaEl.closest ? mediaEl.closest("tr") : null;
      if (row) {
        row.classList.add("media-outage-row");
        if (!row.querySelector(".media-outage-notice")) {
          var cells = row.querySelectorAll("td");
          var titleCell = cells.length > 1 ? cells[1] : cells[0];
          if (titleCell) {
            var badge = document.createElement("span");
            badge.className = "media-outage-notice";
            badge.setAttribute("role", "alert");
            badge.textContent = "⚠️ " + t.trackError;
            titleCell.appendChild(badge);
          }
        }
      }
    } else if (mediaEl.tagName === "VIDEO") {
      var lightbox = mediaEl.closest
        ? mediaEl.closest(".video-lightbox")
        : null;
      if (lightbox) {
        lightbox.classList.add("media-outage-state");
        var figure = lightbox.querySelector("figure");
        if (figure && !figure.querySelector(".media-outage-notice")) {
          var vidBadge = document.createElement("div");
          vidBadge.className = "media-outage-notice";
          vidBadge.setAttribute("role", "alert");
          vidBadge.textContent = "⚠️ " + t.trackError;
          figure.appendChild(vidBadge);
        }
      }
    } else if (mediaEl.tagName === "IMG") {
      var fig = mediaEl.closest ? mediaEl.closest("figure") : null;
      if (fig) {
        fig.classList.add("media-outage-state");
        if (!fig.querySelector(".media-outage-notice")) {
          var imgBadge = document.createElement("div");
          imgBadge.className = "media-outage-notice";
          imgBadge.setAttribute("role", "alert");
          imgBadge.textContent = "⚠️ " + t.trackError;
          fig.appendChild(imgBadge);
        }
      }
    }

    setOutageCached(true);
    try {
      sessionStorage.removeItem(STORAGE_DISMISSED_KEY);
    } catch (e) {}
    showOutageBanner();

    try {
      var customEvt = new CustomEvent("daugavpils:media-error", {
        detail: {
          track: mediaEl.dataset.track || "",
          video: mediaEl.dataset.video || "",
          band: mediaEl.dataset.band || "",
          release: mediaEl.dataset.release || "",
          code: mediaEl.error ? mediaEl.error.code : 0,
          src: src,
        },
      });
      document.dispatchEvent(customEvt);
    } catch (e) {}
  }

  document.addEventListener(
    "error",
    function (event) {
      if (
        event.target &&
        (event.target.tagName === "AUDIO" ||
          event.target.tagName === "VIDEO" ||
          event.target.tagName === "SOURCE" ||
          event.target.tagName === "IMG")
      ) {
        handleMediaError(event.target);
      }
    },
    true
  );

  document.addEventListener("DOMContentLoaded", function () {
    if (isOutageCached() && !isOutageDismissed()) {
      showOutageBanner();
    }

    // Check for images that already failed before player.js executed (e.g. script defer)
    var imgs = document.querySelectorAll("img");
    for (var i = 0; i < imgs.length; i++) {
      var img = imgs[i];
      if (
        img.src &&
        img.src.indexOf("archive.org") !== -1 &&
        img.complete &&
        img.naturalWidth === 0
      ) {
        handleMediaError(img);
      }
    }
  });

  window.DaugavpilsArchiveOutage = {
    showBanner: showOutageBanner,
    retry: retryArchiveConnection,
    handleError: handleMediaError,
    probe: probeArchiveOrg,
    isOutageCached: isOutageCached,
    setOutageCached: setOutageCached,
  };
})();
