const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('NexDesktop',Object.freeze({
 show:(service,bounds)=>ipcRenderer.invoke('service:show',service,bounds),
 hide:()=>ipcRenderer.invoke('service:hide'),bounds:bounds=>ipcRenderer.invoke('service:bounds',bounds),
 back:()=>ipcRenderer.invoke('service:back'),reload:()=>ipcRenderer.invoke('service:reload'),
 route:(service,from,to)=>ipcRenderer.invoke('service:route',service,from,to),
 prepareUpdate:()=>ipcRenderer.invoke('update:prepare'),applyUpdate:state=>ipcRenderer.invoke('update:apply',state),
 exportPdf:(html,name)=>ipcRenderer.invoke('document:pdf',html,name),
 exportXlsx:(bytes,name)=>ipcRenderer.invoke('document:xlsx',bytes,name),
 saveFile:(bytes,name)=>ipcRenderer.invoke('document:file',bytes,name),
 copy:text=>ipcRenderer.invoke('clipboard:write',text),
 notify:value=>ipcRenderer.invoke('notification:show',value),
 clearNotifications:()=>ipcRenderer.invoke('notification:clear'),
 onNotification:callback=>{const listener=(_event,value)=>callback(value);ipcRenderer.on('notification:open',listener);return ()=>ipcRenderer.removeListener('notification:open',listener)},
 quit:()=>ipcRenderer.invoke('application:quit')
}));
