import http from 'node:http';
import worker from '../worker/index.js';
import {session,login,logout,sameOrigin,loginPage} from './auth.mjs';
const CMS=new URL(process.env.CMS_ORIGIN||'https://gt-detailing.romanmontanov1.chatgpt.site');
if(CMS.protocol!=='https:'&&!(process.env.NODE_ENV==='test'&&CMS.hostname==='127.0.0.1'))throw Error('CMS_ORIGIN must use HTTPS');
const port=Number(process.env.PORT||3000),cache=new Map();
const publicPath=p=>p==='/'||p==='/index.html'||['/services','/studio','/works','/contacts','/robots.txt','/sitemap.xml'].includes(p)||/^\/(services\/[a-z0-9-]+|works\/\d+|assets\/[a-z0-9-]+\.(jpg|png|webp)|media\/[a-f0-9-]{36})$/.test(p);
const send=(res,status,headers,body,head)=>{res.writeHead(status,headers);res.end(head?undefined:body)};
function documentResponse(text,origin){return text.replaceAll(CMS.origin+'/sitemap.xml',origin+'/sitemap.xml').replaceAll('<loc>'+CMS.origin,'<loc>'+origin).replace('</head>','<link rel="canonical" href="'+origin.replace(/[<>&"']/g,'')+'__PATH__"></head>')}
async function readBody(req,max=5*1024*1024){const chunks=[];let size=0;for await(const c of req){size+=c.length;if(size>max)throw Error('Слишком большой запрос');chunks.push(c)}return Buffer.concat(chunks)}
const server=http.createServer(async(req,res)=>{
 const local=new URL(req.url,'http://localhost'),p=local.pathname.replace(/\/$/,'')||'/',head=req.method==='HEAD';
 const common={'X-Content-Type-Options':'nosniff','Referrer-Policy':'strict-origin-when-cross-origin'};
 const origin=process.env.PUBLIC_SITE_URL||'https://'+(req.headers.host||'localhost');
 const authHeaders={...common,'Content-Type':'text/html;charset=utf-8','Cache-Control':'no-store','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"};
 const jsonReply=(status,value,extra={})=>send(res,status,{...authHeaders,'Content-Type':'application/json;charset=utf-8',...extra},JSON.stringify(value),head);
 if(p==='/healthz')return jsonReply(200,{ok:true});
 if(p==='/api/auth/login'&&req.method==='POST'){
  if(!sameOrigin(req,origin))return jsonReply(403,{error:'Запрос отклонён'});
  if(!String(req.headers['content-type']).startsWith('application/json'))return jsonReply(415,{error:'Ожидается JSON'});
  try{const input=JSON.parse(await readBody(req,4096));const result=await login(input.email,input.password,req);return jsonReply(result.status,result.error?{error:result.error}:{ok:true},result.cookie?{'Set-Cookie':result.cookie}:{})}catch{return jsonReply(400,{error:'Не удалось обработать вход'})}
 }
 if(p==='/api/auth/logout'&&req.method==='POST'){
  if(!sameOrigin(req,origin))return jsonReply(403,{error:'Запрос отклонён'});
  return send(res,303,{...authHeaders,'Location':'/admin','Set-Cookie':logout(req)},'',head);
 }
 const isAdmin=p==='/admin'||p==='/admin.html',isApi=p==='/api/content'||p==='/api/upload';
 if(isAdmin||isApi){
  if(!session(req)){if(isAdmin&&['GET','HEAD'].includes(req.method))return send(res,200,authHeaders,loginPage,head);return jsonReply(401,{error:'Сессия завершена. Войдите заново.'})}
  if(isAdmin&&!['GET','HEAD'].includes(req.method))return jsonReply(405,{error:'Метод не поддерживается'});
  if(isApi&&!['GET','HEAD'].includes(req.method)&&(!sameOrigin(req,origin)||req.headers['x-gt-admin']!=='1'))return jsonReply(403,{error:'Запрос отклонён'});
  try{
   const body=['GET','HEAD'].includes(req.method)?undefined:await readBody(req);
   const headers={'X-GT-Service-Key':process.env.CMS_SERVICE_KEY||'','Origin':CMS.origin,'X-GT-Admin':'1'};
   for(const name of ['content-type','if-match'])if(req.headers[name])headers[name]=req.headers[name];
   const upstream=await fetch(new URL(isAdmin?'/admin':p,CMS),{method:head?'GET':req.method,headers,body,redirect:'manual',signal:AbortSignal.timeout(20000)});
   if(isAdmin&&upstream.status!==200)return jsonReply(502,{error:'Не удалось открыть редактор. Повторите запрос.'});
   let bytes=Buffer.from(await upstream.arrayBuffer());
   if(isAdmin){let html=bytes.toString();html=html.replace('<div class="actions">','<div class="actions"><form action="/api/auth/logout" method="post" style="margin:0"><button type="submit" aria-label="Выйти из админки">Выйти</button></form>');bytes=Buffer.from(html)}
   return send(res,upstream.status,{...authHeaders,'Content-Type':upstream.headers.get('content-type')||'application/json'},bytes,head);
  }catch{return jsonReply(503,{error:'Сервер хранения временно недоступен. Изменения не сохранены. Повторите запрос.'})}
 }
 if(!['GET','HEAD'].includes(req.method)){req.resume();return jsonReply(405,{error:'Метод не поддерживается'})}
 if(!publicPath(p))return send(res,404,{...common,'Content-Type':'text/html;charset=utf-8'},'<!doctype html><html lang="ru"><meta charset="utf-8"><title>Страница не найдена</title><body style="background:#101112;color:white;font:18px Arial;padding:10%"><h1>Страница не найдена</h1><a href="/" style="color:#ed83c2">На главную</a></body></html>',head);
 const key=p;
 try{
  let item=cache.get(key);
  if(!item||Date.now()-item.time>10000){
   try{
    const upstream=await fetch(new URL(p,CMS),{redirect:'manual',signal:AbortSignal.timeout(8000),headers:{'Accept':p.includes('.')?'*/*':'text/html'}});
    if(upstream.status!==200&&upstream.status!==404)throw Error('CMS unavailable');
    const bytes=Buffer.from(await upstream.arrayBuffer());
    item={status:upstream.status,bytes,headers:Object.fromEntries(['content-type','content-security-policy','cache-control'].map(k=>[k,upstream.headers.get(k)]).filter(([,v])=>v)),time:Date.now()};
    if(cache.size>=60)cache.delete(cache.keys().next().value);cache.set(key,item);
   }catch{
    if(!item){
     // The checked-in Worker provides all public pages and local photographs as a read-only fallback.
     const fallback=await worker.fetch(new Request(origin+p),{});
     item={status:fallback.status,bytes:Buffer.from(await fallback.arrayBuffer()),headers:Object.fromEntries(fallback.headers),time:0};
     if(p.startsWith('/media/'))item.status=503;
    }
   }
  }
  const headers={...common,...item.headers};delete headers['content-length'];delete headers['set-cookie'];
  headers['Cache-Control']=p.startsWith('/assets/')?'public,max-age=86400':'no-store';
  let bytes=item.bytes;
  if(/text\/html|xml|text\/plain/.test(headers['content-type']||headers['Content-Type']||'')){
   let text=bytes.toString();
   if((headers['content-type']||headers['Content-Type']||'').includes('html'))text=documentResponse(text,origin).replace('__PATH__',p);
   else text=text.replaceAll(CMS.origin,origin);
   bytes=Buffer.from(text);
  }
  send(res,item.status,headers,bytes,head);
 }catch{send(res,503,{...common,'Content-Type':'text/plain;charset=utf-8'},'Сайт временно недоступен. Повторите запрос.',head)}
});
server.listen(port,'0.0.0.0',()=>console.log('GT public frontend listening on '+port));
process.on('SIGTERM',()=>{server.close(()=>process.exit(0));setTimeout(()=>process.exit(0),10000).unref()});
