/* shared theme toggle (persisted) — pages include a <button id="themeTgl"> in the top bar */
(function(){
  var r=document.documentElement, K="tk-theme", saved=localStorage.getItem(K);
  if(saved) r.setAttribute("data-theme", saved);
  function wire(){
    var b=document.getElementById("themeTgl");
    if(!b) return;
    b.onclick=function(){
      var cur=r.getAttribute("data-theme")||(matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light");
      var nxt=cur==="dark"?"light":"dark";
      r.setAttribute("data-theme", nxt); localStorage.setItem(K, nxt);
    };
  }
  if(document.readyState!=="loading") wire();
  else document.addEventListener("DOMContentLoaded", wire);
})();
