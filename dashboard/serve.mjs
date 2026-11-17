// Preview the production build using Node only; no package installation required.
import http from 'node:http';
import {readFile,stat} from 'node:fs/promises';
import {resolve,extname,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('./dist/kolej/browser/',import.meta.url));
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.woff2':'font/woff2'};
const server=http.createServer(async(req,res)=>{
 try{
  const pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname);
  let path=resolve(root,'.'+pathname);
  if(path!==resolve(root)&&!path.startsWith(resolve(root)+sep)){res.writeHead(403);res.end();return;}
  if(path===resolve(root))path=resolve(root,'index.html');
  try{if(!(await stat(path)).isFile())throw Error('not a file');}catch{res.writeHead(404);res.end('Not found');return;}
  const body=await readFile(path);res.writeHead(200,{'Content-Type':types[extname(path)]??'application/octet-stream','Cache-Control':'no-cache','X-Content-Type-Options':'nosniff'});res.end(body);
 }catch{res.writeHead(400);res.end('Bad request');}
});
server.listen(4200,'127.0.0.1',()=>console.log('Dashboard: http://127.0.0.1:4200/'));
