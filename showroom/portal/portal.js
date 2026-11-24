"use strict";
const byId = id => document.getElementById(id);
const terminal = byId("terminal");
const guide = byId("guide");
let settings;
let noticeTimer;
function notice(message) {
  const box = byId("message");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { box.hidden = true; }, 5000);
}
function select(work) {
  const showTerminal = work === "terminal";
  byId("terminalPanel").hidden = !showTerminal;
  byId("consolePanel").hidden = showTerminal;
  byId("terminalTab").setAttribute("aria-pressed", String(showTerminal));
  byId("consoleTab").classList.toggle("chosen", !showTerminal);
  if (showTerminal) requestAnimationFrame(() => terminal.contentWindow.term?.fit());
}
byId("terminalTab").addEventListener("click", () => select("terminal"));
byId("consoleTab").addEventListener("click", event => {
  if (!settings) { event.preventDefault(); return; }
  select("console");
  if (settings.embeddedConsole) {
    event.preventDefault();
    const frame = byId("console");
    if (!frame.hasAttribute("src")) frame.src = settings.embeddedConsole;
    frame.hidden = false;
    byId("consoleHint").hidden = true;
  }
});
// The supplied RHDP theme emits a Run button for AsciiDoc listings with the execute role.
// Use ttyd's xterm input API. The guide and terminal stay mounted when the tab changes.
function wireGuide() {
  let doc;
  try { doc = guide.contentDocument; } catch { return; }
  if (!doc?.documentElement || doc.documentElement.dataset.grlabWired) return;
  doc.documentElement.dataset.grlabWired = "1";
  doc.addEventListener("click", event => {
    const button = event.target.closest?.(".execute .paste-button");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const code = button.closest(".listingblock")?.querySelector("pre code");
    if (!code) return;
    select("terminal");
    const term = terminal.contentWindow.term;
    // ttyd sets its title once the WebSocket has finished the server handshake.
    if (!term || !terminal.contentDocument?.title.startsWith("Guardrail terminal")) {
      notice("Terminal is connecting. Wait for the shell prompt, then run the command again.");
      return;
    }
    term.focus();
    term.input(code.textContent.trimEnd().replace(/\n/g, "\r") + "\r");
  }, true);
}
guide.addEventListener("load", wireGuide);
wireGuide();
(async () => {
  try {
    const response = await fetch("/session", {cache:"no-store"});
    if (!response.ok) throw new Error("Session unavailable");
    settings = await response.json();
    byId("identity").textContent = settings.owner + " · " + settings.namespace;
    byId("consoleTab").href = settings.console;
    byId("externalConsole").href = settings.console;
    if (!settings.embeddedConsole) byId("consoleTab").setAttribute("aria-label", "OCP Console (opens in a new browser tab)");
    terminal.src = "/terminal/";
  } catch {
    byId("identity").textContent = "Sign-in required";
    notice("Reopen the workshop URL to sign in to OpenShift.");
  }
})();
