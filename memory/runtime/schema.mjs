export const MAX_RECORD_BYTES = 4096;
export function fail(code, message) { throw Object.assign(new Error(message), {code}); }
export function safeRelative(value) {
 if(typeof value !== 'string' || !value || value.includes('\\') || value.includes(':') || value.startsWith('/') || value.split('/').some(p=>!p || p==='.' || p==='..') || /[\x00-\x1f]/.test(value)) fail('INVALID','Unsafe relative path');
 return value;
}
const fields = {DEC:['decision','reason','confirmation','supersedes'],ERR:['symptom','cause','correction','verification'],TASK:['objective','progress','pending','blockers','owner','branch'],ARCH:['responsibility','entries','relations','coverage']};
const statuses={DEC:['accepted','superseded','rejected'],ERR:['resolved','monitoring','obsolete'],TASK:['active','blocked','completed','cancelled'],ARCH:['verified','partial','not-mapped']};
const common=['schemaVersion','id','type','title','module','terms','status','summary','origin','references','created','updated'];
function text(v) { if(typeof v!=='string'||!v.trim()) fail('INVALID','Expected nonempty text'); }
function date(v) {if(typeof v!=='string'||!/^\d{4}-\d\d-\d\dT/.test(v)||!Number.isFinite(Date.parse(v))) fail('INVALID','Invalid ISO date');}
function strings(a, paths=false) {if(!Array.isArray(a)||a.length>32) fail('INVALID','Expected bounded list'); for(const v of a) paths?safeRelative(v):text(v);}
export function validateRecord(input) {
 const r=structuredClone(input); if(!r||!fields[r.type]) fail('INVALID','Unknown record type');
 const encoded=JSON.stringify(r); if(Buffer.byteLength(encoded)>MAX_RECORD_BYTES) fail('BUDGET','Record exceeds 4 KiB');
 if(/gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{20,}|-----BEGIN .*PRIVATE KEY|eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}/.test(encoded)) fail('SECRET','Obvious secret pattern');
 if(Object.keys(r).some(k=>![...common,...fields[r.type]].includes(k))) fail('INVALID','Unknown field');
 if(r.schemaVersion!==1||!new RegExp(`^${r.type}-[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`).test(r.id)||!statuses[r.type].includes(r.status)) fail('INVALID','Invalid version, ID or status');
 for(const k of ['title','module','summary']) text(r[k]); safeRelative(r.module);strings(r.terms);strings(r.references,true);date(r.created);date(r.updated);if(Date.parse(r.updated)<Date.parse(r.created)) fail('INVALID','Dates reversed');
 if(!['user-confirmed','repository-verified','unverified'].includes(r.origin)) fail('INVALID','Invalid origin');
 if(r.type==='DEC') {for(const k of ['decision','reason','confirmation']) text(r[k]);if(r.status==='accepted'&&r.origin==='unverified')fail('INVALID','Accepted decision needs confirmation');if(Object.hasOwn(r,'supersedes')&&(typeof r.supersedes!=='string'||!/^DEC-[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(r.supersedes)))fail('INVALID','Invalid supersedes');}
 if(r.type==='ERR') {if(Object.hasOwn(r,'verification')){if(!r.verification||typeof r.verification!=='object'||Array.isArray(r.verification)||Object.keys(r.verification).some(k=>!['command','result','verifiedAt'].includes(k)))fail('INVALID','Invalid verification');for(const k of ['command','result'])text(r.verification[k]);date(r.verification.verifiedAt);}for(const k of ['symptom','cause','correction'])text(r[k]);if(r.status==='resolved'){if(r.origin==='unverified'||!r.verification)fail('INVALID','Resolved error needs verification');for(const k of ['command','result'])text(r.verification[k]);date(r.verification.verifiedAt);if(r.verification.result!=='passed')fail('INVALID','Verification must pass');}}
 if(r.type==='TASK'){if(Object.hasOwn(r,'branch')){text(r.branch);if(r.branch.length>256)fail('INVALID','Branch exceeds 256 characters');}for(const k of ['objective','progress','owner'])text(r[k]);strings(r.pending);strings(r.blockers);if(['completed','cancelled'].includes(r.status)&&(r.pending.length||r.blockers.length))fail('INVALID','Terminal task cannot have pending work');}
 if(r.type==='ARCH'){text(r.responsibility);strings(r.entries,true);if(!Array.isArray(r.relations)||r.relations.length>32)fail('INVALID','Invalid relations');for(const link of r.relations){if(Object.keys(link).some(k=>!['target','summary','origin','references'].includes(k)))fail('INVALID','Unknown relation field');safeRelative(link.target);text(link.summary);strings(link.references,true);if(!['repository-verified','unverified'].includes(link.origin)||link.origin==='repository-verified'&&!link.references.length)fail('INVALID','Relation provenance missing');}if(!r.coverage||Object.keys(r.coverage).some(k=>!['inspected','excluded'].includes(k)))fail('INVALID','Missing or invalid coverage');strings(r.coverage.inspected,true);strings(r.coverage.excluded,true);if(r.status==='verified'&&(r.origin!=='repository-verified'||!r.references.length))fail('INVALID','Verified architecture needs sources');}
 return r;
}
