'use strict';
const LIMIT=64*1024*1024;
function documentBytes(value,excel=false){
 let bytes;
 if(value instanceof ArrayBuffer)bytes=Buffer.from(value);
 else if(ArrayBuffer.isView(value)&&value.BYTES_PER_ELEMENT===1)bytes=Buffer.from(value.buffer,value.byteOffset,value.byteLength);
 else throw Error('Не удалось получить файл из интерфейса');
 if(!bytes.length||bytes.length>LIMIT)throw Error('Размер файла должен быть от 1 байта до 64 МБ');
 if(excel&&(bytes[0]!==80||bytes[1]!==75))throw Error('Хост вернул файл в неверном формате Excel');
 return Buffer.from(bytes);
}
function documentName(value){if(typeof value!=='string'||!value||value.length>200)throw Error('Некорректное имя файла');return value.replace(/[\\/:*?"<>|\x00-\x1f]/g,'-')}
module.exports={documentBytes,documentName};
