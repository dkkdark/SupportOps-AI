export const isDemo=document.querySelector('meta[name="supportops-mode"]').content==='demo';
export let config={};
let demo;
export async function init(){
  if(isDemo){demo=(await import('./demo.js')).demo; await demo.init(); config={mode:'demo',provider:'baseline',store:'Листва',version:'0.1.0',faults_enabled:true};}
  else config=await request('/config');
  return config;
}
export async function request(path,options={}){
  if(isDemo)return demo.request(path,options);
  const key=sessionStorage.getItem('supportops-api-key')||'';
  const r=await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...(key?{'X-API-Key':key}:{}),...options.headers},body:options.body?JSON.stringify(options.body):undefined});
  if(!r.ok){const b=await r.json().catch(()=>({})); const e=new Error(typeof b.detail==='string'?b.detail:'Проверьте данные и повторите попытку');e.status=r.status;throw e;}
  return r.json();
}
