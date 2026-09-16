// Handles admin user management confirmation dialogs without inline scripts (GitHub issue #70).
(function () {
  "use strict";

  function initConfirmButtons() {
    var buttons = document.querySelectorAll("button[data-confirm]");
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      btn.addEventListener("click", function (e) {
        var message = this.getAttribute("data-confirm");
        if (message && !window.confirm(message)) {
          e.preventDefault();
        }
      });
    }
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initConfirmButtons);
    } else {
      initConfirmButtons();
    }
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { initConfirmButtons };
  }
})();
