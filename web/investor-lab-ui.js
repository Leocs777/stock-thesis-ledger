(() => {
  "use strict";

  function setPressed(group, state) {
    group.querySelectorAll("[data-demo-state]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.demoState === state));
    });
  }

  function updateButtonDemo(group, state) {
    const buttons = group.querySelectorAll(".lab-button");
    buttons.forEach((button) => {
      button.disabled = state === "disabled" || state === "loading";
      button.classList.toggle("is-focus-demo", state === "focus");
      if (!button.dataset.defaultLabel) button.dataset.defaultLabel = button.textContent.trim();
      button.textContent = state === "loading" ? "Working…" : button.dataset.defaultLabel;
      button.setAttribute("aria-busy", String(state === "loading"));
    });
  }

  function updateInputDemo(group, state) {
    const field = group.querySelector(".lab-field");
    const input = group.querySelector(".lab-input");
    const helper = group.querySelector(".lab-field__helper");
    if (!field || !input || !helper) return;
    field.dataset.state = state;
    input.disabled = state === "disabled";
    input.classList.toggle("is-focus-demo", state === "focus");
    input.setAttribute("aria-invalid", String(state === "error"));
    helper.textContent = state === "error" ? "Enter a valid OCC option symbol." : "Supports symbol, price, quantity, and contract entry.";
  }

  function updateTableDemo(group, state) {
    const body = group.querySelector("tbody");
    if (!body) return;
    const rows = {
      populated: '<tr><td>AAPL</td><td>Equity</td><td data-align="right">2</td><td data-align="right">$214.20</td></tr><tr><td>SPY 500C</td><td>Option</td><td data-align="right">1</td><td data-align="right">$3.85</td></tr>',
      empty: '<tr><td colspan="4"><div class="lab-table__empty">No paper positions yet.</div></td></tr>',
      loading: '<tr><td colspan="4"><div class="lab-skeleton"></div></td></tr><tr><td colspan="4"><div class="lab-skeleton" style="width:72%"></div></td></tr>'
    };
    body.innerHTML = rows[state] || rows.populated;
  }

  function initComponentDemos(root = document) {
    root.querySelectorAll("[data-demo-group]").forEach((group) => {
      group.addEventListener("click", (event) => {
        const control = event.target.closest("[data-demo-state]");
        if (!control || !group.contains(control)) return;
        const state = control.dataset.demoState;
        setPressed(group, state);
        if (group.dataset.demoGroup === "button") updateButtonDemo(group, state);
        if (group.dataset.demoGroup === "input") updateInputDemo(group, state);
        if (group.dataset.demoGroup === "table") updateTableDemo(group, state);
      });
    });
  }

  window.InvestorLabUI = { initComponentDemos };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => initComponentDemos());
  } else {
    initComponentDemos();
  }
})();
