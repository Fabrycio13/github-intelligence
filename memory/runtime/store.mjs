import * as fs from 'node:fs/promises';
import path from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {validateRecord,fail} from './schema.mjs';
const folders={DEC:'decisions',ERR:'errors',TASK:'tasks',ARCH:'architecture/modules'};
const hash=s=>createHash('sha256').update(s).digest('hex');
const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
function exact(value,keys){return value!==null&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).length===keys.length&&keys.every(k=>Object.hasOwn(value,k));}
function validateEnvelope(envelope,file){
 if(!exact(envelope,['record','operations']))fail('CORRUPT','Invalid envelope fields');
 try{validateRecord(envelope.record);}catch{fail('CORRUPT','Invalid stored record');}
 const r=envelope.record,relative=idParts(r.id).join('/');
 if(!file.split(path.sep).join('/').endsWith('/'+relative))fail('CORRUPT','Stored identity does not match path');
 if(!Array.isArray(envelope.operations)||!envelope.operations.length||envelope.operations.length>32)fail('CORRUPT','Invalid receipts');
 const ids=new Set();
 for(const receipt of envelope.operations){
  if(!exact(receipt,['id','payloadHash','result'])||typeof receipt.id!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(receipt.id)||ids.has(receipt.id)||typeof receipt.payloadHash!=='string'||!/^[0-9a-f]{64}$/.test(receipt.payloadHash))fail('CORRUPT','Invalid receipt identity or hash');
  ids.add(receipt.id);const result=receipt.result;
  if(!exact(result,['ok','operationId','recordIds','changedPaths','hash','verified','warnings'])||result.ok!==true||result.verified!==true||result.operationId!==receipt.id||result.hash!==receipt.payloadHash||!Array.isArray(result.recordIds)||result.recordIds.length!==1||result.recordIds[0]!==r.id||!Array.isArray(result.changedPaths)||result.changedPaths.length!==1||result.changedPaths[0]!==relative||!Array.isArray(result.warnings)||result.warnings.length!==0)fail('CORRUPT','Invalid receipt result');
 }
 if(envelope.operations.at(-1).payloadHash!==hash(canonical(r)))fail('CORRUPT','Latest receipt does not match record');
}
function idParts(id){if(typeof id!=='string'||!/^(DEC|ERR|TASK|ARCH)-[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(id))fail('INVALID','Invalid record ID');return ['memory',...folders[id.split('-')[0]].split('/'),`${id}.md`];}
async function checked(root,parts,create=false){let current=root;for(let i=0;i<parts.length;i++){current=path.join(current,parts[i]);let stat;try{stat=await fs.lstat(current);}catch(e){if(e.code!=='ENOENT')throw e;if(create&&i<parts.length-1){try{await fs.mkdir(current);}catch(e){if(e.code!=='EEXIST')throw e;}stat=await fs.lstat(current);}else continue;}if(stat&&(stat.isSymbolicLink()||(i<parts.length-1&&!stat.isDirectory())||(i===parts.length-1&&!stat.isFile())))fail('UNSAFE_PATH','Symlink or invalid managed path');}return current;}
async function rootPath(root){if(!path.isAbsolute(root))fail('INVALID','Root must be absolute');const canonicalRoot=await fs.realpath(root);if(!(await fs.stat(canonicalRoot)).isDirectory())fail('INVALID','Root must be directory');return canonicalRoot;}
async function load(file,maxBytes=24576){let h;try{h=await fs.open(file,'r');const stat=await h.stat();if(!stat.isFile()||stat.nlink!==1)fail('UNSAFE_PATH','Invalid file or hardlink');if(stat.size>Math.min(24576,maxBytes))fail('BUDGET','Stored envelope too large');const buffer=Buffer.alloc(stat.size);let offset=0;while(offset<buffer.length){const chunk=await h.read(buffer,offset,buffer.length-offset,offset);if(!chunk.bytesRead)fail('CORRUPT','File changed during read');offset+=chunk.bytesRead;}const bytes=buffer.toString('utf8');const match=/^# Project memory v1\n\n```json\n([\s\S]*)\n```\n$/.exec(bytes);if(!match)fail('CORRUPT','Invalid stored format');let envelope;try{envelope=JSON.parse(match[1]);}catch{fail('CORRUPT','Invalid stored JSON');}validateEnvelope(envelope,file);return {envelope,hash:hash(canonical(envelope.record)),bytesRead:Buffer.byteLength(bytes)};}catch(e){if(e.code==='ENOENT')return null;throw e;}finally{await h?.close();}}
async function lock(root,timeout){const lockPath=path.join(root,'memory','.core.lock');const start=Date.now();for(;;){try{await fs.mkdir(lockPath);const token=randomUUID();const ownerPath=path.join(lockPath,'owner.json');try{await fs.writeFile(ownerPath,JSON.stringify({pid:process.pid,token,created:new Date().toISOString()}),{flag:'wx',mode:0o600});}catch(e){throw e;}return async()=>{const owner=JSON.parse(await fs.readFile(ownerPath,'utf8'));if(owner.token!==token)fail('LOCK_OWNER','Lock ownership changed; retained');await fs.unlink(ownerPath);await fs.rmdir(lockPath);};}catch(e){if(e.code!=='EEXIST')throw e;if(Date.now()-start>=timeout)fail('LOCK_TIMEOUT','Lock retained; manual owner investigation required');await new Promise(r=>setTimeout(r,10));}}}
export async function readRecord(root,id,{maxBytes=24576}={}){if(!Number.isInteger(maxBytes)||maxBytes<0||maxBytes>262144)fail('INVALID','Invalid read budget');root=await rootPath(root);const file=await checked(root,idParts(id));const found=await load(file,maxBytes);return found?{record:found.envelope.record,hash:found.hash,bytesRead:found.bytesRead,path:path.relative(root,file).split(path.sep).join('/')}:null;}
export async function writeRecord(root,operation,{lockTimeoutMs=1000,beforePublish}={}){
 const r=validateRecord(operation.record);const {operationId,expectedHash}=operation;
 if(typeof operationId!=='string'||!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(operationId)||!(expectedHash===null||typeof expectedHash==='string'&&/^[0-9a-f]{64}$/.test(expectedHash)))fail('INVALID','Canonical lowercase operationId UUID and explicit expectedHash required');
 if(!Number.isInteger(lockTimeoutMs)||lockTimeoutMs<0||lockTimeoutMs>10000)fail('INVALID','Lock timeout out of bounds');
 root=await rootPath(root);const parts=idParts(r.id);let file=await checked(root,parts,true);const release=await lock(root,lockTimeoutMs);let temporary;
 try{file=await checked(root,parts);const previous=await load(file);const payloadHash=hash(canonical(r));const prior=previous?.envelope.operations.find(x=>x.id===operationId);
 if(prior){if(prior.payloadHash!==payloadHash)fail('OPERATION_CONFLICT','Operation ID reused with different payload');return prior.result;}
 if((previous?.hash??null)!==expectedHash)fail('HASH_CONFLICT','Record changed; reread and reconcile');
 const operations=previous?.envelope.operations??[];if(operations.length>=32)fail('BUDGET','32 operation receipts reached; explicit future compaction required');
 const result={ok:true,operationId,recordIds:[r.id],changedPaths:[path.relative(root,file).split(path.sep).join('/')],hash:payloadHash,verified:true,warnings:[]};
 const envelope={record:r,operations:[...operations,{id:operationId,payloadHash,result}]};validateEnvelope(envelope,file);const bytes=`# Project memory v1\n\n\`\`\`json\n${canonical(envelope)}\n\`\`\`\n`;if(Buffer.byteLength(bytes)>24576)fail('BUDGET','Envelope exceeds 24 KiB');
 temporary=path.join(path.dirname(file),`.tmp-${randomUUID()}`);const handle=await fs.open(temporary,'wx',0o600);try{await handle.writeFile(bytes);await handle.sync();}finally{await handle.close();}
 if(beforePublish)await beforePublish();
 await checked(root,parts);await fs.rename(temporary,file);temporary=null;
 const published=await load(file);if(published?.hash!==payloadHash||canonical(published.envelope)!==canonical(envelope))fail('VERIFY_FAILED','Published target did not match');return result;
 }finally{if(temporary)await fs.unlink(temporary).catch(()=>{});await release();}
}
