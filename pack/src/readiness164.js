/* Registered before dependencies, so a failed script cannot leave a blank tab. */
(()=>{
 window.NexPackReady=false;window.NexPackFailed=false;
 window.NexPackStatus=status=>{
  window.NexPackReady=status==='ready';window.NexPackFailed=status==='failed';
  if(window.parent!==window)window.parent.postMessage({type:'nex:pack-status',status,instance:location.search},location.origin);
 };
 window.addEventListener('error',event=>{
  if(!window.NexPackReady&&(event.target?.tagName==='SCRIPT'||event.error))window.NexPackStatus('failed');
 },true);
 window.addEventListener('DOMContentLoaded',()=>setTimeout(()=>{
  if(window.NexPackFailed)return;
  try{
   if(!document.querySelector('#scene canvas')||typeof project==='undefined'||!project.trucks.length||!document.getElementById('btnCalc').onclick)throw Error('Pack startup incomplete');
   window.dispatchEvent(new Event('resize'));window.NexPackStatus('ready');
  }catch{window.NexPackStatus('failed')}
 },0));
})();
