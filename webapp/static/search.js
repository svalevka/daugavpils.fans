(function () {
  "use strict";

  var RU_TO_LATIN = {
    а: "a", б: "b", в: "v", г: "g", д: "d", е: "e", ё: "e", ж: "zh",
    з: "z", и: "i", й: "y", к: "k", л: "l", м: "m", н: "n", о: "o",
    п: "p", р: "r", с: "s", т: "t", у: "u", ф: "f", х: "kh", ц: "ts",
    ч: "ch", ш: "sh", щ: "shch", ъ: "", ы: "y", ь: "", э: "e", ю: "yu", я: "ya"
  };

  var LATIN_TO_CYR = [
    ["shch", "щ"], ["sch", "щ"], ["sh", "ш"], ["ch", "ч"], ["zh", "ж"],
    ["kh", "х"], ["ts", "ц"], ["tz", "ц"], ["ya", "я"], ["yu", "ю"],
    ["yo", "ё"], ["jo", "ё"], ["ju", "ю"], ["ja", "я"], ["je", "е"],
    ["ph", "ф"], ["th", "т"],
    ["a", "а"], ["b", "б"], ["c", "ц"], ["d", "д"], ["e", "е"],
    ["f", "ф"], ["g", "г"], ["h", "х"], ["i", "и"], ["j", "й"],
    ["k", "к"], ["l", "л"], ["m", "м"], ["n", "н"], ["o", "о"],
    ["p", "п"], ["r", "р"], ["s", "с"], ["t", "т"], ["u", "у"],
    ["v", "в"], ["w", "в"], ["y", "ы"], ["z", "з"]
  ];

  function cyrToLat(str) {
    if (!str) return "";
    var res = "";
    var s = str.toLowerCase();
    for (var i = 0; i < s.length; i++) {
      var c = s[i];
      res += RU_TO_LATIN[c] !== undefined ? RU_TO_LATIN[c] : c;
    }
    return res;
  }

  function normalizePhonetic(str) {
    if (!str) return "";
    return str
      .toLowerCase()
      .replace(/ё/g, "е")
      .replace(/io/g, "yo")
      .replace(/jo/g, "yo")
      .replace(/yo/g, "e")
      .replace(/shch/g, "shc")
      .replace(/sch/g, "shc")
      .replace(/sh/g, "sh")
      .replace(/ch/g, "ch")
      .replace(/zh/g, "zh")
      .replace(/kh/g, "h")
      .replace(/ph/g, "f")
      .replace(/ts/g, "c")
      .replace(/tz/g, "c")
      .replace(/ia/g, "ya")
      .replace(/ja/g, "ya")
      .replace(/iu/g, "yu")
      .replace(/ju/g, "yu")
      .replace(/w/g, "v")
      .replace(/[yj]/g, "i");
  }

  function latToCyr(str) {
    if (!str) return "";
    var s = str.toLowerCase();
    for (var i = 0; i < LATIN_TO_CYR.length; i++) {
      var pair = LATIN_TO_CYR[i];
      s = s.split(pair[0]).join(pair[1]);
    }
    return s;
  }

  function textMatches(target, query) {
    if (!target || !query) return false;
    var tNorm = target.toLowerCase().replace(/ё/g, "е");
    var qNorm = query.toLowerCase().replace(/ё/g, "е");
    if (tNorm.indexOf(qNorm) !== -1) return true;

    var tPhone = normalizePhonetic(cyrToLat(target));
    var qPhone = normalizePhonetic(cyrToLat(query));
    if (tPhone.indexOf(qPhone) !== -1) return true;

    var tCyr = latToCyr(target).replace(/ё/g, "е");
    var qCyr = latToCyr(query).replace(/ё/g, "е");
    if (tCyr.indexOf(qCyr) !== -1) return true;

    return false;
  }

  function getItemSearchTexts(item) {
    var texts = [];
    if (item.name) texts.push(item.name);
    if (item.band) texts.push(item.band);
    if (item.release) texts.push(item.release);
    if (item.year) texts.push(String(item.year));
    if (item.years) texts.push(String(item.years));
    if (item.alternate_name) texts.push(item.alternate_name);
    if (item.alternate_names && Array.isArray(item.alternate_names)) {
      texts.push.apply(texts, item.alternate_names);
    }
    if (item.genres && Array.isArray(item.genres)) {
      texts.push.apply(texts, item.genres);
    }
    if (item.members && Array.isArray(item.members)) {
      texts.push.apply(texts, item.members);
    }
    if (item.roles && Array.isArray(item.roles)) {
      texts.push.apply(texts, item.roles);
    }
    if (item.credits && Array.isArray(item.credits)) {
      texts.push.apply(texts, item.credits);
    }
    return texts;
  }

  function scoreItem(item, queryWords) {
    // Check if every query word matches at least one search text
    var texts = getItemSearchTexts(item);
    for (var w = 0; w < queryWords.length; w++) {
      var word = queryWords[w];
      var wordMatched = false;
      for (var t = 0; t < texts.length; t++) {
        if (textMatches(texts[t], word)) {
          wordMatched = true;
          break;
        }
      }
      if (!wordMatched) return -1;
    }

    // Rank score: lower is better
    var fullQuery = queryWords.join(" ");
    var nameLower = (item.name || "").toLowerCase().replace(/ё/g, "е");
    var qLower = fullQuery.toLowerCase().replace(/ё/g, "е");

    if (nameLower === qLower) return 1;
    if (nameLower.indexOf(qLower) === 0) return 2;
    if (textMatches(item.name, fullQuery)) return 3;
    if (item.band && textMatches(item.band, fullQuery)) return 4;
    return 5;
  }

  function searchIndex(index, query) {
    if (!index || !query) return { bands: [], releases: [], tracks: [], total: 0 };
    var words = query.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) return { bands: [], releases: [], tracks: [], total: 0 };

    var matches = [];
    for (var i = 0; i < index.length; i++) {
      var item = index[i];
      var score = scoreItem(item, words);
      if (score > 0) {
        matches.push({ item: item, score: score });
      }
    }

    matches.sort(function (a, b) {
      return a.score - b.score;
    });

    var bands = [];
    var releases = [];
    var tracks = [];

    for (var m = 0; m < matches.length; m++) {
      var it = matches[m].item;
      if (it.type === "band" && bands.length < 5) {
        bands.push(it);
      } else if (it.type === "release" && releases.length < 5) {
        releases.push(it);
      } else if (it.type === "track" && tracks.length < 8) {
        tracks.push(it);
      }
    }

    return {
      bands: bands,
      releases: releases,
      tracks: tracks,
      total: bands.length + releases.length + tracks.length
    };
  }

  function initSearch(container) {
    if (!container) return;
    var input = container.querySelector(".search-input");
    var clearBtn = container.querySelector(".search-clear");
    var resultsEl = container.querySelector(".search-results");
    if (!input || !resultsEl) return;

    var indexUrl = input.getAttribute("data-index-url") || "/search-index.json";
    var labelBands = container.getAttribute("data-label-bands") || "Bands";
    var labelReleases = container.getAttribute("data-label-releases") || "Releases";
    var labelTracks = container.getAttribute("data-label-tracks") || "Tracks";
    var labelNoResults = container.getAttribute("data-label-no-results") || "No results found";

    var cachedIndex = null;
    var fetchPromise = null;
    var selectedIndex = -1;

    function loadIndex() {
      if (cachedIndex) return Promise.resolve(cachedIndex);
      if (fetchPromise) return fetchPromise;

      fetchPromise = fetch(indexUrl)
        .then(function (res) {
          if (!res.ok) throw new Error("Search index HTTP " + res.status);
          return res.json();
        })
        .then(function (data) {
          cachedIndex = data;
          return data;
        })
        .catch(function (err) {
          console.warn("Failed to load search index:", err);
          cachedIndex = [];
          return [];
        });

      return fetchPromise;
    }

    function renderResults(results) {
      selectedIndex = -1;
      resultsEl.innerHTML = "";

      if (results.total === 0) {
        var noRes = document.createElement("div");
        noRes.className = "search-no-results";
        noRes.textContent = labelNoResults;
        resultsEl.appendChild(noRes);
        resultsEl.hidden = false;
        return;
      }

      function appendGroup(title, items, renderMeta) {
        if (items.length === 0) return;
        var group = document.createElement("div");
        group.className = "search-group";

        var heading = document.createElement("div");
        heading.className = "search-group-heading";
        heading.textContent = title;
        group.appendChild(heading);

        for (var i = 0; i < items.length; i++) {
          var it = items[i];
          var a = document.createElement("a");
          a.className = "search-result-item";
          a.href = it.url;

          var titleSpan = document.createElement("div");
          titleSpan.className = "search-result-title";
          titleSpan.textContent = it.name;
          a.appendChild(titleSpan);

          var metaText = renderMeta(it);
          if (metaText) {
            var metaSpan = document.createElement("div");
            metaSpan.className = "search-result-meta";
            metaSpan.textContent = metaText;
            a.appendChild(metaSpan);
          }

          group.appendChild(a);
        }
        resultsEl.appendChild(group);
      }

      appendGroup(labelBands, results.bands, function (it) {
        var parts = [];
        if (it.years) parts.push(it.years);
        if (it.genres && it.genres.length > 0) parts.push(it.genres.join(", "));
        return parts.join(" · ");
      });

      appendGroup(labelReleases, results.releases, function (it) {
        var parts = [];
        if (it.band) parts.push(it.band);
        if (it.year) parts.push(it.year);
        return parts.join(" · ");
      });

      appendGroup(labelTracks, results.tracks, function (it) {
        var parts = [];
        if (it.band) parts.push(it.band);
        if (it.release) parts.push(it.release);
        return parts.join(" — ");
      });

      resultsEl.hidden = false;
    }

    function doSearch() {
      var query = input.value.trim();
      if (clearBtn) clearBtn.hidden = query.length === 0;

      if (!query) {
        resultsEl.innerHTML = "";
        resultsEl.hidden = true;
        selectedIndex = -1;
        return;
      }

      loadIndex().then(function (index) {
        var results = searchIndex(index, query);
        renderResults(results);
      });
    }

    function updateSelection(newIndex) {
      var items = resultsEl.querySelectorAll(".search-result-item");
      if (items.length === 0) return;

      if (selectedIndex >= 0 && selectedIndex < items.length) {
        items[selectedIndex].classList.remove("selected");
        items[selectedIndex].removeAttribute("aria-selected");
      }

      if (newIndex < 0) {
        selectedIndex = -1;
        input.removeAttribute("aria-activedescendant");
        return;
      }

      selectedIndex = (newIndex + items.length) % items.length;
      var selectedItem = items[selectedIndex];
      selectedItem.classList.add("selected");
      selectedItem.setAttribute("aria-selected", "true");
      selectedItem.scrollIntoView({ block: "nearest" });
    }

    // Prefetch search index on focus / pointerdown
    input.addEventListener("focus", function () {
      loadIndex();
      if (input.value.trim().length > 0) {
        doSearch();
      }
    });

    input.addEventListener("input", doSearch);

    input.addEventListener("keydown", function (e) {
      var items = resultsEl.querySelectorAll(".search-result-item");
      if (e.key === "ArrowDown") {
        e.preventDefault();
        updateSelection(selectedIndex + 1);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        updateSelection(selectedIndex - 1);
      } else if (e.key === "Enter") {
        if (selectedIndex >= 0 && selectedIndex < items.length) {
          e.preventDefault();
          items[selectedIndex].click();
        } else if (items.length > 0) {
          e.preventDefault();
          items[0].click();
        }
      } else if (e.key === "Escape") {
        resultsEl.hidden = true;
        selectedIndex = -1;
      }
    });

    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        input.value = "";
        clearBtn.hidden = true;
        resultsEl.innerHTML = "";
        resultsEl.hidden = true;
        selectedIndex = -1;
        input.focus();
      });
    }

    document.addEventListener("click", function (e) {
      if (!container.contains(e.target)) {
        resultsEl.hidden = true;
        selectedIndex = -1;
      }
    });
  }

  // Auto-init on page load
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", function () {
        initSearch(document.querySelector(".site-search"));
      });
    } else {
      initSearch(document.querySelector(".site-search"));
    }
  }

  // Expose internals for testing and extensibility
  if (typeof window !== "undefined") {
    window.DaugavpilsSearch = {
      cyrToLat: cyrToLat,
      latToCyr: latToCyr,
      normalizePhonetic: normalizePhonetic,
      textMatches: textMatches,
      scoreItem: scoreItem,
      searchIndex: searchIndex,
      initSearch: initSearch
    };
  }
})();
