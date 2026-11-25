"use strict";
const byId = id => document.getElementById(id);
const terminal = byId("terminal"), guide = byId("guide");
const layout = byId("workspaceLayout"), splitter = byId("splitter");
let settings, timer;
const guideWidthKey = "grlab-showroom-guide-width";
const resizeLayout = window.matchMedia("(min-width: 761px)");
function widthBounds() {
  const available = layout.clientWidth - splitter.getBoundingClientRect().width;
  const minimum = Math.min(280, available / 2);
  return {available, min:Math.max(available * .22, minimum), max:Math.min(available * .72, available - Math.min(360, available / 2))};
}
function setGuideWidth(width, save=false) {
  if(!resizeLayout.matches) return;
  const {available,min,max}=widthBounds();
  if(available<=0 || max<min) return;
  const value=Math.min(max,Math.max(min,width));
  const percent=Math.round(value/available*100);
  layout.style.setProperty("--guide-width",`${value}px`);
  splitter.setAttribute("aria-valuemin",String(Math.round(min/available*100)));
  splitter.setAttribute("aria-valuemax",String(Math.round(max/available*100)));
  splitter.setAttribute("aria-valuenow",String(percent));
  splitter.setAttribute("aria-valuetext",`Instructions ${percent}%, workspace ${100-percent}%`);
  if(save) try{localStorage.setItem(guideWidthKey,String(value));}catch{}
}
function currentGuideWidth() {
  const width=parseFloat(getComputedStyle(layout).getPropertyValue("--guide-width"));
  return Number.isFinite(width) ? width : widthBounds().available*.46;
}
function resetGuideWidth() {
  let saved;
  try{saved=Number(localStorage.getItem(guideWidthKey));}catch{}
  const {available}=widthBounds();
  setGuideWidth(Number.isFinite(saved) && saved>0 ? saved : available*.46);
}
resetGuideWidth();
window.addEventListener("resize",()=>setGuideWidth(currentGuideWidth()));
splitter.addEventListener("pointerdown",e=>{
  if(!resizeLayout.matches || e.button!==0) return;
  splitter.setPointerCapture(e.pointerId); document.body.classList.add("resizing"); e.preventDefault();
});
splitter.addEventListener("pointermove",e=>{
  if(!splitter.hasPointerCapture(e.pointerId)) return;
  const rect=layout.getBoundingClientRect();
  setGuideWidth(e.clientX-rect.left-splitter.getBoundingClientRect().width/2);
});
function finishResize(e) {
  if(!splitter.hasPointerCapture(e.pointerId)) return;
  splitter.releasePointerCapture(e.pointerId); document.body.classList.remove("resizing");
  setGuideWidth(currentGuideWidth(),true);
}
splitter.addEventListener("pointerup",finishResize);
splitter.addEventListener("pointercancel",finishResize);
splitter.addEventListener("keydown",e=>{
  if(!resizeLayout.matches) return;
  const {min,max}=widthBounds(); let width=currentGuideWidth();
  if(e.key==="ArrowLeft") width-=e.shiftKey?48:16;
  else if(e.key==="ArrowRight") width+=e.shiftKey?48:16;
  else if(e.key==="Home") width=min;
  else if(e.key==="End") width=max;
  else return;
  e.preventDefault(); setGuideWidth(width,true);
});
splitter.addEventListener("dblclick",()=>{
  const {available}=widthBounds(); setGuideWidth(available*.46,true);
});
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
