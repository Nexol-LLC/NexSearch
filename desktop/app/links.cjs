'use strict';
function externalUrl(value){
 if(typeof value!=='string'||/[\u0000-\u001f\u007f]/.test(value))return null;
 const raw=value.trim().replace(/&amp;/g,'&');if(!raw||/^[\\/](?!\/)/.test(raw))return null;
 // A colon in a host:port is not an executable URI scheme.
 if(/^[a-z][a-z0-9+.-]*:/i.test(raw)&&!/^https?:\/\//i.test(raw)&&!/^[-a-z0-9.]+:\d+(?:\/|$)/i.test(raw))return null;
 try{const u=new URL(raw.startsWith('//')?'https:'+raw:/^https?:\/\//i.test(raw)?raw:'https://'+raw);return ['http:','https:'].includes(u.protocol)&&u.hostname&&!u.username&&!u.password?u.href:null}catch{return null}
}
module.exports={externalUrl};
