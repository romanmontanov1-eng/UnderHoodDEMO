import assert from 'node:assert/strict';import http from 'node:http';import {spawn} from 'node:child_process';import {randomBytes,scryptSync,createHash} from 'node:crypto';import fs from 'node:fs';import worker from '../worker/index.js';
const key=randomBytes(32).toString('hex'),password=randomBytes(20).toString('hex'),salt=randomBytes(16).toString('hex'),records=new Map();
const BUCKET={async get(k){const o=records.get(k);return o?{...o,json:async()=>JSON.parse(o.body.toString())}:null},async put(k,b,opts={}){const old=records.get(k);if(opts.onlyIf?.etagMatches&&old?.etag!==opts.onlyIf.etagMatches)return null;if(opts.onlyIf?.etagDoesNotMatch==='*'&&old)return null;const body=Buffer.from(b),etag=createHash('sha256').update(body).digest('hex');records.set(k,{body,etag,httpMetadata:opts.httpMetadata});return{etag}}};
const upstream=http.createServer(async(req,res)=>{const chunks=[];for await(const c of req)chunks.push(c);const body=Buffer.concat(chunks);const r=await worker.fetch(new Request('http://127.0.0.1:3198'+req.url,{method:req.method,headers:req.headers,...(!['GET','HEAD'].includes(req.method)?{body}:{} )}),{BUCKET,CMS_SERVICE_KEY:key});res.writeHead(r.status,Object.fromEntries(r.headers));res.end(Buffer.from(await r.arrayBuffer()))});
await new Promise(resolve=>upstream.listen(3198,'127.0.0.1',resolve));let child;
async function start(){child=spawn(process.execPath,['render/server.mjs'],{env:{...process.env,NODE_ENV:'test',PORT:'3199',CMS_ORIGIN:'http://127.0.0.1:3198',PUBLIC_SITE_URL:'https://gt.example',ADMIN_EMAIL:'owner@example.test',ADMIN_PASSWORD_HASH:salt+':'+scryptSync(password,salt,64).toString('hex'),CMS_SERVICE_KEY:key},stdio:['ignore','pipe','pipe']});await new Promise((resolve,reject)=>{child.stdout.once('data',resolve);child.once('error',reject);child.once('exit',code=>reject(Error('Server exited '+code)))})}
async function stop(){await new Promise(resolve=>{child.once('exit',resolve);child.kill('SIGTERM')})}
let cookie='';const call=(p,opts={})=>fetch('http://127.0.0.1:3199'+p,{...opts,headers:{Cookie:cookie,...opts.headers}});const headers={'Content-Type':'application/json',Origin:'https://gt.example','X-GT-Admin':'1'};
async function signin(){const r=await call('/api/auth/login',{method:'POST',headers,body:JSON.stringify({email:'owner@example.test',password})});assert.equal(r.status,200);const value=r.headers.get('set-cookie');assert.match(value,/HttpOnly; Secure; SameSite=Strict/);cookie=value.split(';')[0]}
try{await start();
 const html=await(await call('/admin')).text();assert.match(html,/autocomplete="current-password"/);assert.ok(!html.includes('signin-with-chatgpt'));
 assert.equal((await call('/api/content')).status,401);
 assert.equal((await call('/api/content',{headers:{'oai-authenticated-user-id':'fake','oai-authenticated-user-email':'owner@example.test','X-GT-Service-Key':key}})).status,401);
 assert.equal((await call('/api/auth/login',{method:'POST',headers:{...headers,Origin:'https://evil.test'},body:'{}'})).status,403);
 assert.equal((await call('/api/auth/login',{method:'POST',headers,body:JSON.stringify({email:'owner@example.test',password:'wrong'})})).status,401);
 await signin();assert.match(await(await call('/admin')).text(),/api\/auth\/logout/);
 const current=await(await call('/api/content')).json();assert.ok(current.revision);const edited={...current.content,heroText:'Проверка сохранения после входа'};
 assert.equal((await call('/api/content',{method:'PUT',headers:{...headers,Origin:'https://evil.test','If-Match':current.revision},body:JSON.stringify(edited)})).status,403);
 const save=await call('/api/content',{method:'PUT',headers:{...headers,'If-Match':current.revision},body:JSON.stringify(edited)});assert.equal(save.status,200);
 assert.equal((await call('/api/content',{method:'PUT',headers:{...headers,'If-Match':current.revision},body:JSON.stringify(edited)})).status,409);
 const upload=await call('/api/upload',{method:'POST',headers:{...headers,'Content-Type':'image/jpeg'},body:fs.readFileSync('hero.jpg')});assert.equal(upload.status,200);const image=(await upload.json()).url;assert.equal((await call(image)).status,200);
 await stop();await start();assert.equal((await call('/api/content')).status,401);await signin();assert.equal((await(await call('/api/content')).json()).content.heroText,edited.heroText);
 const out=await call('/api/auth/logout',{method:'POST',headers,redirect:'manual'});assert.equal(out.status,303);assert.equal((await call('/api/content')).status,401);
 for(let i=0;i<9;i++){const r=await call('/api/auth/login',{method:'POST',headers,body:JSON.stringify({email:'owner@example.test',password:'wrong'})});assert.equal(r.status,i<8?401:429)}
 console.log('PASS: password login; secure cookies; forged headers denied; CSRF; admin editor; content save; conflicts; image upload; settings survive frontend restart; logout invalidates session; rate limiting');
}finally{if(child?.exitCode===null)await stop();await new Promise(resolve=>upstream.close(resolve))}
