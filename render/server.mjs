import http from 'node:http';
import worker from '../worker/index.js';
const CMS=new URL(process.env.CMS_ORIGIN||'https://gt-detailing.romanmontanov1.chatgpt.site');
if(CMS.protocol!=='https:')throw Error('CMS_ORIGIN must use HTTPS');
const port=Number(process.env.PORT||3000),cache=new Map();
const publicPath=p=>p==='/'||p==='/index.html'||['/services','/studio','/works','/contacts','/robots.txt','/sitemap.xml'].includes(p)||/^\/(services\/[a-z0-9-]+|works\/\d+|assets\/[a-z0-9-]+\.(jpg|png|webp)|media\/[a-f0-9-]{36})$/.test(p);
const send=(res,status,headers,body,head)=>{res.writeHead(status,headers);res.end(head?undefined:body)};
function documentResponse(text,origin){return text.replaceAll(CMS.origin+'/sitemap.xml',origin+'/sitemap.xml').replaceAll('<loc>'+CMS.origin,'<loc>'+origin).replace('</head>','<link rel="canonical" href="'+origin.replace(/[<>&"']/g,'')+'__PATH__"></head>')}
const server=http.createServer(async(req,res)=>{
 const local=new URL(req.url,'http://localhost'),p=local.pathname.replace(/\/$/,'')||'/',head=req.method==='HEAD';
 const common={'X-Content-Type-Options':'nosniff','Referrer-Policy':'strict-origin-when-cross-origin'};
 if(!['GET','HEAD'].includes(req.method)){req.resume();return send(res,405,{...common,'Allow':'GET, HEAD','Content-Type':'application/json'},JSON.stringify({error:'Редактирование доступно в админке владельца'}),head)}
 if(p==='/healthz')return send(res,200,{...common,'Content-Type':'application/json','Cache-Control':'no-store'},'{"ok":true}',head);
 if(p==='/admin'||p==='/admin.html')return send(res,302,{...common,'Location':new URL('/admin',CMS).href,'Cache-Control':'no-store'},'',head);
 if(!publicPath(p))return send(res,404,{...common,'Content-Type':'text/html;charset=utf-8'},'<!doctype html><html lang="ru"><meta charset="utf-8"><title>Страница не найдена</title><body style="background:#101112;color:white;font:18px Arial;padding:10%"><h1>Страница не найдена</h1><a href="/" style="color:#ed83c2">На главную</a></body></html>',head);
 const origin=process.env.PUBLIC_SITE_URL||'https://'+(req.headers.host||'localhost'),key=p;
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
