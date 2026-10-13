/* Dimensions of usable lower decks, not the overall vehicle length.
   Source: Техника ооо негабаритика 06.pdf, pages 2–9. Heights are loading heights. */
const TRAILER_SPECS=[
 ['jumba4','Юмба · 4 оси',45000,12000,2500,900,16000,24000,2],
 ['megajumba4','Мега-Юмба · 4 оси',45000,12000,2500,900,16000,24000,3],
 ['mega4','Мега · 4 оси',45000,12000,2500,1100,16000,24000,4],
 ['lowbed5','Трал · 5 осей',65000,11800,2500,900,16000,24000,5],
 ['lowbed7','Трал · 7 осей',95000,10650,2500,950,16000,24000,6],
 ['beam','Балковоз',65000,15875,2500,1560,16000,35000,7],
 ['blade','Лопастевоз',68000,16410,2500,1560,16000,59000,8],
 ['modular','Модульный трал',300000,9000,3000,300,26000,26000,9]
];
for(const [id,name,maxPayload,L,W,loadingHeight,overallMin,overallMax,page] of TRAILER_SPECS){
 const v={id:'pdf-'+id,group:'truck',body:'flatbed',name:{ru:name,en:name},L,W,H:4000,maxPayload,axles:null,
  trailer:{loadingHeight,overallMin,overallMax,maxW:id==='beam'||id==='blade'?2500:3000,page,
   note:id==='modular'?'Нижняя площадка 5 + 4 м; модули выше площадки.':id==='lowbed7'?'Грузоподъёмность 95 т по подробному листу; на обзорном листе указано 102 т.':'Прямоугольник нижней площадки. Гусак и уступы не включены.'}};
 VEHICLE_TEMPLATES.push(v);
 VEHICLE_CATALOG.push({name:'Трал · '+name,cls:'Негабарит / трал',gp:maxPayload,L,W,H:4000,vol:null,templateId:v.id});
}
function updateTrailerControls(){
 const v=vehicleById($('vehSel').value),spec=v?.trailer,box=$('trailerSettings');
 box.hidden=!spec;if(!spec)return;
 $('trailerL').value=v.L;$('trailerL').min=v.L;$('trailerL').max=v.L+spec.overallMax-spec.overallMin;
 $('trailerW').value=v.W;$('trailerW').min=v.W;$('trailerW').max=spec.maxW;
 $('trailerH').value=v.H;
 $('trailerInfo').textContent=`Высота площадки: ${spec.loadingHeight} мм. Габаритная длина: ${spec.overallMin/1000}–${spec.overallMax/1000} м. ${spec.note} Нагрузки на оси требуют схемы конкретного прицепа.`;
 $('trailerPdf').href='docs/trailers.pdf#page='+spec.page;
}
function configuredTrailer(v){
 const spec=v.trailer;if(!spec)return v;
 const L=+$('trailerL').value,W=+$('trailerW').value,H=+$('trailerH').value;
 if(L<v.L||L>v.L+spec.overallMax-spec.overallMin||W<v.W||W>spec.maxW||H<1){alert('Проверьте размеры площадки трала.');return null;}
 return {...v,L,W,H,trailer:{...spec},axles:null};
}
