importScripts('models.js','cargo-geometry.js','axles.js','running-gear.js','packer.js','planner14.js');
self.onmessage=event=>{try{self.postMessage({request:event.data.request,plans:alternativePlans(event.data)})}catch(error){self.postMessage({request:event.data.request,error:error.message})}};
