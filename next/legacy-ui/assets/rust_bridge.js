/* Native transport adapter. The 1.6.7 interface and its feature scripts are retained. */
(()=>{
 'use strict';
 const invoke=(name,args={})=>window.__TAURI__.core.invoke(name,args),originalFetch=window.fetch.bind(window);
 document.addEventListener('DOMContentLoaded',()=>{
  document.title='NexSearch 2.0';
  const brand=document.querySelector('.brand small');if(brand)brand.textContent='версия 2.0';
  const mark=document.querySelector('.auth-card .eyebrow');if(mark)mark.textContent='NEXSEARCH · 2.0';
 });
 window.fetch=async(input,options={})=>{
  const request=input instanceof Request?input:null,url=new URL(request?.url||String(input),location.href);
  if(url.origin!==location.origin||!url.pathname.startsWith('/api/'))return originalFetch(input,options);
  const signal=options.signal||request?.signal;if(signal?.aborted)throw new DOMException('Запрос отменён','AbortError');
  const method=options.method||request?.method||'GET',body=options.body??(request&&!['GET','HEAD'].includes(method)?await request.clone().text():null);
  if(body!==null&&typeof body!=='string')throw Error('Этот формат запроса ещё переносится на Rust');
  const reply=await invoke('legacy_request',{path:url.pathname+url.search,method,body});
  if(signal?.aborted)throw new DOMException('Запрос отменён','AbortError');
  const bytes=Uint8Array.from(atob(reply.body),c=>c.charCodeAt(0));
  return new Response([204,205,304].includes(reply.status)?null:bytes,{status:reply.status,headers:reply.headers});
 };
 // Until server event streaming is ported, the existing 1.6.7 polling fallback
 // remains active. Never pretend a stream is connected.
 window.EventSource=class extends EventTarget {
  static CONNECTING=0;static OPEN=1;static CLOSED=2;readyState=2;
  constructor(){super()}close(){this.readyState=2}
 };
 window.NexDesktop=Object.freeze({
  hide:()=>invoke('service_hide'),bounds:bounds=>invoke('service_bounds',{bounds}),route:()=>Promise.resolve(false),
  show:(key,bounds)=>key.startsWith('personal:')?invoke('custom_service_show',{id:key.slice(9),bounds}):invoke('service_show',{key,bounds}),
  back:()=>invoke('service_back'),reload:()=>invoke('service_reload'),
  copy:text=>invoke('copy_text',{text:String(text)}),
  saveFile:(bytes,name)=>invoke('save_file',{bytes:Array.from(bytes),name}),
  exportXlsx:(bytes,name)=>invoke('save_file',{bytes:Array.from(bytes),name}),
  quit:()=>invoke('quit_app'),notify:()=>Promise.resolve(),clearNotifications:()=>Promise.resolve(),onNotification:()=>{}
 });
 // Tauri's asset protocol addresses a directory differently from the old HTTP
 // server. Normalize only the PackOnTruck document URL, preserving its query.
 const source=Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype,'src');
 Object.defineProperty(HTMLIFrameElement.prototype,'src',{...source,set(value){const url=new URL(value,location.href);if(url.origin===location.origin&&url.pathname==='/pack/')url.pathname='/pack/index.html';source.set.call(this,url.href)}});
 document.addEventListener('click',event=>{const link=event.target.closest('a[href]');if(!link)return;const url=new URL(link.href,location.href);if(['http:','https:'].includes(url.protocol)&&url.origin!==location.origin){event.preventDefault();invoke('open_external',{url:url.href}).catch(error=>{if(window.console)console.error(String(error))})}},true);
})();
