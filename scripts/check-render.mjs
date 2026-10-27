import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
const child=spawn(process.execPath,['render/server.mjs'],{env:{...process.env,PORT:'3197',CMS_ORIGIN:'https://127.0.0.1:1',PUBLIC_SITE_URL:'https://gt.example'},stdio:['ignore','pipe','pipe']});
try{
 await new Promise((resolve,reject)=>{child.stdout.once('data',resolve);child.once('error',reject);child.once('exit',code=>reject(Error('Server exited '+code)))});
 const call=(path,opts)=>fetch('http://127.0.0.1:3197'+path,opts);
 assert.equal((await call('/healthz')).status,200);
 for(const path of ['/','/services','/services/polishing','/works','/studio','/contacts']){const response=await call(path);assert.equal(response.status,200,path);const html=await response.text();assert.match(html,/<h1/);assert.match(html,/rel="canonical"/);assert.ok(!html.includes('data-owner-only'));}
 const admin=await call('/admin',{redirect:'manual'});assert.equal(admin.status,200);assert.match(await admin.text(),/autocomplete="current-password"/);
 assert.equal((await call('/api/content',{headers:{'oai-authenticated-user-id':'fake','oai-authenticated-user-email':'owner@example.test'}})).status,401);
 assert.equal((await call('/api/content',{method:'PUT',body:'{}'})).status,401);
 assert.equal((await call('/assets/studio.webp')).headers.get('content-type'),'image/webp');
 assert.match(await(await call('/sitemap.xml')).text(),/https:\/\/gt.example\/services/);
 assert.equal((await call('/not-a-page')).status,404);
 assert.equal((await call('/media/00000000-0000-0000-0000-000000000000')).status,503);
 console.log('PASS: Render frontend starts; fallback routes and assets; canonical and sitemap; password login page; private API and writes blocked; missing media reported');
}finally{child.kill('SIGTERM')}
