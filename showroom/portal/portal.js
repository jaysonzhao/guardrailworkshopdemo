"use strict";
const byId = id => document.getElementById(id);
const terminal = byId("terminal"), guide = byId("guide");
let settings, timer;
function notice(message) {
  byId("message").textContent=message; byId("message").hidden=false;
  clearTimeout(timer); timer=setTimeout(()=>{byId("message").hidden=true;},5000);
}
function select(which) {
  const on = which === "terminal";
  byId("terminalPanel").hidden = !on; byId("consolePanel").hidden = on;
  byId("terminalTab").setAttribute("aria-pressed",String(on)); byId("consoleTab").classList.toggle("chosen",!on);
  if(on) requestAnimationFrame(()=>terminal.contentWindow.term?.fit());
}
byId("terminalTab").addEventListener("click",()=>select("terminal"));
byId("consoleTab").addEventListener("click",e=>{
  if(!settings){ e.preventDefault(); return; }
  select("console");
  if(settings.embeddedConsole){
    e.preventDefault(); const frame=byId("console");
    if(!frame.hasAttribute("src")) frame.src=settings.embeddedConsole;
    frame.hidden=false; byId("consoleHint").hidden=true;
  }
});
function wireGuide(){
  let doc;
  try{doc=guide.contentDocument;}catch{return;}
  if(!doc?.documentElement || doc.documentElement.dataset.grlabWired) return;
  doc.documentElement.dataset.grlabWired="1";
  doc.addEventListener("click",e=>{
    const button=e.target.closest?.(".execute .paste-button");
    if(!button) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const code=button.closest(".listingblock")?.querySelector("pre code");
    if(!code) return;
    select("terminal");
    const shell=terminal.contentWindow.term;
    if(!shell || !terminal.contentDocument?.title.startsWith("Guardrail terminal")){
      notice("Wait for the terminal prompt, then run the command again."); return;
    }
    shell.focus(); shell.input(code.textContent.trimEnd().replace(/\n/g,"\r")+"\r");
  },true);
}
guide.addEventListener("load",wireGuide); wireGuide();
(async()=>{
  try{
    const r=await fetch("/session",{cache:"no-store"}); if(!r.ok) throw new Error();
    settings=await r.json(); byId("identity").textContent=settings.owner+" · "+settings.namespace;
    byId("consoleTab").href=settings.console; byId("externalConsole").href=settings.console;
    terminal.src="/terminal/";
  }catch{ byId("identity").textContent="Sign-in required"; notice("Reopen the workshop URL to sign in to OpenShift."); }
})();
