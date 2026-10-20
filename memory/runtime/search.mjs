import * as fs from 'node:fs/promises';
import path from 'node:path';
import {readRecord} from './store.mjs';
import {fail} from './schema.mjs';
const folders={DEC:'decisions',ERR:'errors',TASK:'tasks',ARCH:'architecture/modules'};
export async function searchRecords(root,{query='',type,module,limit=5,maxScan=100,maxBytes=65536,includeInactive=false}={}){
 if(typeof query!=='string'||query.length>128||type&&!folders[type]||module!==undefined&&typeof module!=='string'||typeof includeInactive!=='boolean')fail('INVALID','Invalid query');
 for(const [v,min,max] of [[limit,1,10],[maxScan,1,1000],[maxBytes,1,262144]])if(!Number.isInteger(v)||v<min||v>max)fail('INVALID','Invalid search budget');
 if(!path.isAbsolute(root))fail('INVALID','Root must be absolute');root=await fs.realpath(root);let scanned=0,bytesRead=0;const records=[];
 const words=query.toLowerCase().split(/\s+/).filter(Boolean);
 for(const kind of type?[type]:Object.keys(folders)){
  const parts=['memory',...folders[kind].split('/')];let dir=root;let absent=false;for(const part of parts){dir=path.join(dir,part);try{const s=await fs.lstat(dir);if(s.isSymbolicLink()||!s.isDirectory())fail('UNSAFE_PATH','Unsafe search directory');}catch(e){if(e.code==='ENOENT'){absent=true;break;}throw e;}}if(absent)continue;
  const entries=await fs.opendir(dir);try{for await(const entry of entries){if(scanned>=maxScan||records.length>=limit)return {records,scanned,bytesRead,partial:true};scanned++;if(!entry.name.endsWith('.md'))continue;if(entry.isSymbolicLink()||!entry.isFile())fail('UNSAFE_PATH','Unsafe search entry');
   const id=entry.name.slice(0,-3);if(!new RegExp(`^${kind}-[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`).test(id))fail('CORRUPT','Search filename does not match directory type');let found;try{found=await readRecord(root,id,{maxBytes:maxBytes-bytesRead});}catch(e){if(e.code==='BUDGET')return {records,scanned,bytesRead,partial:true};throw e;}if(!found)continue;bytesRead+=found.bytesRead;const r=found.record;
   if(!includeInactive&&['completed','cancelled','superseded','rejected','obsolete'].includes(r.status))continue;if(module&&r.module!==module)continue;const haystack=[r.title,r.summary,r.module,...r.terms].join(' ').toLowerCase();if(!words.every(w=>haystack.includes(w)))continue;
   records.push({id:r.id,type:r.type,title:r.title,module:r.module,status:r.status,summary:r.summary,hash:found.hash,path:found.path});
  }}finally{await entries.close().catch(e=>{if(e.code!=='ERR_DIR_CLOSED')throw e;});}
 }
 return {records,scanned,bytesRead,partial:false};
}
