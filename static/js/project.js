
/* =====================================================
   DEREJE WOOD & CAM MANAGEMENT SYSTEM
   PROJECT MANAGEMENT MODULE
   Interactive JavaScript
   ===================================================== */

"use strict";

document.addEventListener("DOMContentLoaded", function () {

    // ---------------------------------------------
    // 1. Form Validation
    // ---------------------------------------------

    const forms = document.querySelectorAll(".pm-validate-form");

    forms.forEach(function (form) {

        form.addEventListener("submit", function (event) {

            if (!form.checkValidity()) {
                event.preventDefault();
                event.stopPropagation();
            }

            form.classList.add("was-validated");
        });

    });


    // ---------------------------------------------
    // 2. Confirm Delete / Archive Actions
    // ---------------------------------------------

    document.querySelectorAll("[data-pm-confirm]").forEach(function (button) {

        button.addEventListener("click", function (event) {

            const message =
                button.getAttribute("data-pm-confirm") ||
                "Are you sure you want to continue?";

            if (!window.confirm(message)) {
                event.preventDefault();
            }

        });

    });


    // ---------------------------------------------
    // 3. Progress Input Validation
    // ---------------------------------------------

    document.querySelectorAll(".pm-progress-input").forEach(function (input) {

        input.addEventListener("input", function () {

            let value = Number(input.value);

            if (!Number.isFinite(value)) {
                value = 0;
            }

            value = Math.min(100, Math.max(0, value));

            const progressBar = document.querySelector(
                input.getAttribute("data-progress-target")
            );

            if (progressBar) {
                progressBar.style.width = value + "%";
                progressBar.setAttribute("aria-valuenow", value);
            }

        });

    });


    // ---------------------------------------------
    // 4. Table Search
    // ---------------------------------------------

    document.querySelectorAll("[data-pm-search]").forEach(function (searchInput) {

        searchInput.addEventListener("input", function () {

            const targetSelector =
                searchInput.getAttribute("data-pm-search");

            const table = document.querySelector(targetSelector);

            if (!table) return;

            const searchValue = searchInput.value
                .trim()
                .toLowerCase();

            table.querySelectorAll("tbody tr").forEach(function (row) {

                const rowText = row.textContent.toLowerCase();

                row.hidden = !rowText.includes(searchValue);

            });

        });

    });


    // ---------------------------------------------
    // 5. Date Validation
    // ---------------------------------------------

    document.querySelectorAll("[data-pm-start-date]").forEach(function (startInput) {

        const endSelector =
            startInput.getAttribute("data-pm-start-date");

        const endInput = document.querySelector(endSelector);

        if (!endInput) return;

        function validateDates() {

            if (!startInput.value || !endInput.value) {
                endInput.setCustomValidity("");
                return;
            }

            if (endInput.value < startInput.value) {
                endInput.setCustomValidity(
                    "The end date cannot be earlier than the start date."
                );
            } else {
                endInput.setCustomValidity("");
            }

        }

        startInput.addEventListener("change", validateDates);
        endInput.addEventListener("change", validateDates);

    });


    // ---------------------------------------------
    // 6. Task Status Display
    // ---------------------------------------------

    document.querySelectorAll("[data-pm-task-status]").forEach(function (select) {

        select.addEventListener("change", function () {

            const progressSelector =
                select.getAttribute("data-pm-progress-target");

            const progressInput = progressSelector
                ? document.querySelector(progressSelector)
                : null;

            if (!progressInput) return;

            if (select.value === "Completed") {
                progressInput.value = 100;
                progressInput.dispatchEvent(
                    new Event("input", { bubbles: true })
                );
            }

        });

    });


    // ---------------------------------------------
    // 7. Automatic Progress Bar Display
    // ---------------------------------------------

    document.querySelectorAll(".pm-progress-bar").forEach(function (bar) {

        let progress = Number(
            bar.getAttribute("data-progress") || 0
        );

        if (!Number.isFinite(progress)) {
            progress = 0;
        }

        progress = Math.min(100, Math.max(0, progress));

        bar.style.width = progress + "%";
        bar.setAttribute("aria-valuenow", progress);

    });


    // ---------------------------------------------
    // 8. Prevent Accidental Double Form Submission
    // ---------------------------------------------

    document.querySelectorAll("form[data-pm-submit-once]").forEach(function (form) {

        form.addEventListener("submit", function (event) {

            if (!form.checkValidity()) return;

            if (form.dataset.submitted === "true") {
                event.preventDefault();
                return;
            }

            form.dataset.submitted = "true";

            const submitButton = form.querySelector(
                'button[type="submit"], input[type="submit"]'
            );

            if (submitButton) {
                submitButton.disabled = true;
                submitButton.setAttribute("aria-busy", "true");
            }

        });

    });


    // ---------------------------------------------
    // 9. Reset Form Validation
    // ---------------------------------------------

    document.querySelectorAll("[data-pm-reset]").forEach(function (button) {

        button.addEventListener("click", function () {

            const form = button.closest("form");

            if (!form) return;

            form.reset();
            form.classList.remove("was-validated");

            form.querySelectorAll(":invalid").forEach(function (field) {
                field.setCustomValidity("");
            });

        });

    });


    // ---------------------------------------------
    // 10. Initialize Tooltips
    // ---------------------------------------------

    if (window.bootstrap && window.bootstrap.Tooltip) {

        document.querySelectorAll('[data-bs-toggle="tooltip"]')
            .forEach(function (element) {

                new bootstrap.Tooltip(element);

            });

    }

});
