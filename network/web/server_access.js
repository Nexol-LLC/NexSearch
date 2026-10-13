// Direct LAN access on a headless host. Native clients use their local bridge.
if(location.protocol==='https:'){
 const originalFetch=window.fetch.bind(window),store=sessionStorage;
 let device=localStorage.getItem('nexsearch.browser.device');
 if(!device){device=crypto.randomUUID();localStorage.setItem('nexsearch.browser.device',device)}
 window.fetch=async(input,options={})=>{
  const path=typeof input==='string'?input:input.url;
  if(path==='/api/connection')return new Response(JSON.stringify({host_found:true,host_url:location.origin,csrf:'',profile:JSON.parse(store.getItem('nexsearch.profile')||'null')}),{headers:{'Content-Type':'application/json'}});
  if(path==='/api/client/exit')input='/api/auth/logout';
  const token=store.getItem('nexsearch.token');
  options={...options,headers:{...options.headers,...(token?{Authorization:'Bearer '+token}:{})}};
  if(['/api/auth/login','/api/auth/register'].includes(path))options.body=JSON.stringify({...JSON.parse(options.body),device_id:device,computer_name:'Веб · '+navigator.platform});
  const response=await originalFetch(input,options);
  if(['/api/auth/login','/api/auth/register'].includes(path)&&response.ok){const data=await response.clone().json();store.setItem('nexsearch.token',data.token);store.setItem('nexsearch.profile',JSON.stringify(data.employee))}
  if(path==='/api/auth/logout'&&response.ok){store.removeItem('nexsearch.token');store.removeItem('nexsearch.profile')}
  return response;
 };
 document.addEventListener('click',async event=>{
  const link=event.target.closest('a[href^="/api/export"]');if(!link)return;event.preventDefault();
  const response=await fetch(link.getAttribute('href'));if(!response.ok)return;
  const object=URL.createObjectURL(await response.blob()),download=document.createElement('a');download.href=object;download.download='NexSearch.xlsx';download.click();setTimeout(()=>URL.revokeObjectURL(object),3000);
 });
}
