// Drives batch approval and rejection actions on the curator review dashboard (GitHub issue #66).
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var batchForms = document.querySelectorAll(".batch-action-bar");
    if (!batchForms.length) return;

    batchForms.forEach(function (form) {
      var typeInput = form.querySelector('input[name="proposal_type"]');
      var type = typeInput ? typeInput.value : "text";

      var selectAllCheckbox = form.querySelector('[data-select-all="' + type + '"]');
      var countBadge = form.querySelector('[data-selected-count="' + type + '"]');
      var approveBtn = form.querySelector('button[value="approve"]');
      var rejectBtn = form.querySelector('button[value="reject"]');

      function getCheckboxes() {
        return Array.from(document.querySelectorAll('.proposal-select[data-type="' + type + '"]'));
      }

      function updateState() {
        var checkboxes = getCheckboxes();
        var checkedCount = 0;
        var hasOwnSubmission = false;

        checkboxes.forEach(function (cb) {
          if (cb.checked) {
            checkedCount++;
            if (cb.getAttribute("data-own") === "1") {
              hasOwnSubmission = true;
            }
          }
        });

        if (countBadge) {
          countBadge.textContent = "(" + checkedCount + " selected)";
        }

        if (selectAllCheckbox) {
          if (checkboxes.length === 0) {
            selectAllCheckbox.checked = false;
            selectAllCheckbox.indeterminate = false;
          } else if (checkedCount === checkboxes.length) {
            selectAllCheckbox.checked = true;
            selectAllCheckbox.indeterminate = false;
          } else if (checkedCount > 0) {
            selectAllCheckbox.checked = false;
            selectAllCheckbox.indeterminate = true;
          } else {
            selectAllCheckbox.checked = false;
            selectAllCheckbox.indeterminate = false;
          }
        }

        if (approveBtn) {
          approveBtn.disabled = checkedCount === 0 || hasOwnSubmission;
          if (hasOwnSubmission) {
            approveBtn.title = "Cannot approve self-submitted proposal";
          } else {
            approveBtn.removeAttribute("title");
          }
        }

        if (rejectBtn) {
          rejectBtn.disabled = checkedCount === 0;
        }
      }

      if (selectAllCheckbox) {
        selectAllCheckbox.addEventListener("change", function () {
          var checkboxes = getCheckboxes();
          checkboxes.forEach(function (cb) {
            cb.checked = selectAllCheckbox.checked;
          });
          updateState();
        });
      }

      document.addEventListener("change", function (e) {
        if (e.target && e.target.classList.contains("proposal-select") && e.target.getAttribute("data-type") === type) {
          updateState();
        }
      });

      form.addEventListener("submit", function (e) {
        var checkboxes = getCheckboxes();
        var checkedCount = 0;
        var hasOwn = false;

        checkboxes.forEach(function (cb) {
          if (cb.checked) {
            checkedCount++;
            if (cb.getAttribute("data-own") === "1") {
              hasOwn = true;
            }
          }
        });

        if (checkedCount === 0) {
          e.preventDefault();
          alert("Please select at least one proposal.");
          return;
        }

        // Determine which submit button was clicked
        var submitter = e.submitter;
        var action = submitter ? submitter.value : "approve";

        if (action === "approve" && hasOwn) {
          e.preventDefault();
          alert("You cannot approve your own submission. Please uncheck your proposals to approve the rest.");
          return;
        }
      });

      updateState();
    });
  });
})();
