// Browser-only adapter. This is an explicit simulation, not the server or a live model.
let data;
const clone=x=>structuredClone(x);
const save=()=>{try{localStorage.setItem('supportops-demo-v1',JSON.stringify(data))}catch{throw new Error('Не удалось сохранить изменения: хранилище браузера заполнено. Экспортируйте данные и сбросьте демо.')}};
const categories=text=>{const q=text.toLowerCase().replaceAll('ё','е');const rules={delivery:['достав','где','вчера','приш','трек','пути'],cancellation:['отмен','передум','отказ'],returns:['вернуть','возврат','не подош'],damage:['разбит','поврежд','трещин','брак','сломан'],payment:['деньг','денег','денеж','оплат','спис','платеж']};const c=Object.entries(rules).filter(([,words])=>words.some(w=>q.includes(w))).map(([cat])=>cat);return c.length?c:['other'];};
function simulate(t,fault){
 const cats=categories(t.message),day=new Date().toISOString().slice(0,10);let reason=null;
 if(/ignore.{0,30}instructions|system prompt|игнорир\w*.{0,30}инструк|забудь.{0,20}правил/i.test(t.message))reason='untrusted_instructions';
 const found=data.orders.find(o=>o.id===t.order_id);let order=null;
 if(!reason){if(!t.order_id)reason='missing_order_id';else if(fault==='orders_timeout')reason='orders_unavailable';else if(!found)reason='order_not_found';else if(found.customer.toLowerCase().trim()!==t.customer.toLowerCase().trim())reason='ownership_review';else order=clone(found);}
 let sources=[],facts={},required=[],draft=null;
 if(!reason){sources=data.docs.filter(d=>d.effective_from<=day&&(!d.effective_to||d.effective_to>day)&&(cats.includes(d.category)||d.tags.some(tag=>t.message.toLowerCase().includes(tag))));if(fault==='missing_sources')sources=[];
  if(!sources.length)reason='missing_sources';
  else{const statuses={delayed:'задерживается в пути',shipped:'передан перевозчику',processing:'собирается на складе',delivered:'доставлен',cancelled:'отменён'};facts.order_status=`По данным магазина заказ №${order.id} ${statuses[order.status]}.`;required=['order_status'];if(order.tracking)facts.tracking=`Перевозчик — ${order.carrier}. Трек-номер: ${order.tracking}.`;
   const needed=[];
   if(cats.includes('delivery')){needed.push(order.status==='delayed'?'delay_policy':'delivery_window');if(facts.tracking)required.push('tracking')}
   if(cats.includes('cancellation')){if(['shipped','delayed'].includes(order.status))needed.push('cancel_shipped');else if(order.status==='processing')needed.push('cancel_processing');else reason='action_requires_review'}
   if(cats.includes('returns')){needed.push('return_window','return_shipping');if(!order.delivered_at||new Date()-new Date(order.delivered_at)>30*86400000)reason='return_eligibility_review'}
   if(cats.includes('damage'))reason='damage_requires_review';
   if(cats.includes('payment'))reason='payment_requires_review';
   if(['адрес','сертификат','индивидуаль'].some(w=>t.message.toLowerCase().includes(w)))reason='action_requires_review';
   if(cats.length===1&&cats[0]==='other')reason='unsupported_intent';
   const available=Object.assign({},...sources.map(d=>d.facts));for(const key of needed){if(!available[key])reason='missing_sources';else{facts[key]=available[key];required.push(key)}}
  }
 }
 if(!reason&&fault==='invalid_model')reason='invalid_model_output';
 if(!reason){draft='Здравствуйте! Понимаем, что ожидание может быть неудобным.\n\n'+required.map(k=>facts[k]).join('\n\n')+'\n\nЕсли нужна помощь с дальнейшими шагами, пожалуйста, сообщите нам.\nС заботой, команда Листва';sources=sources.filter(d=>required.some(k=>k in d.facts))}
 return {ticket_id:t.id,trace_id:'demo-'+crypto.randomUUID(),order_id:t.order_id,categories:cats,order,sources,facts,required,selection:reason?null:{fact_ids:required,tone:'empathetic'},draft,reason,duration_ms:null,usage:{input_tokens:0,output_tokens:0,cost_usd:0,attempts:fault==='invalid_model'?2:1},checks:[{label:'Данные заказа подтверждены API',passed:!!order},{label:'Использованы действующие правила',passed:!!sources.length},{label:'Все обязательные факты включены',passed:!!draft},{label:'Нет неподтверждённых действий',passed:!!draft}],steps:['classify','orders_api','retrieval',...(!reason?['generate']:[]),'validate'].map(name=>({name,duration_ms:null,status:reason&&name==='validate'?'error':'ok'})),manifest:{code:'browser-demo-v1',prompt:'selection-v1',model:'browser-simulation',documents:Object.fromEntries(sources.map(d=>[d.id,d.version])),evaluation:'supportops-eval-v1'}};
}
function metrics(){const ts=data.tickets,rs=ts.filter(t=>t.result);return {total:ts.length,new:ts.filter(t=>t.status==='new').length,review:ts.filter(t=>t.status==='review').length,approved:ts.filter(t=>t.status==='approved').length,manual:ts.filter(t=>['manual','escalated'].includes(t.status)).length,analyzed:rs.length,p50_ms:null,p95_ms:null,cost_usd:0,tokens:0,runs:rs.map(t=>({ticket_id:t.id,status:t.status,reason:t.result.reason,trace_id:t.result.trace_id,steps:t.result.steps,duration_ms:null,manifest:t.result.manifest}))}}
export const demo={
 async init(){const seed=await fetch('/assets/seed.json').then(r=>r.json());try{const raw=localStorage.getItem('supportops-demo-v1');data=raw?JSON.parse(raw):clone(seed);if(!Array.isArray(data.tickets)||!Array.isArray(data.docs))throw Error('invalid')}catch{data=clone(seed)}},
 async request(path,options={}){
  if(path==='/tickets'&&!options.method)return clone(data.tickets);
  if(path==='/knowledge')return clone(data.docs);
  if(path==='/metrics')return metrics();
  if(path==='/evaluation')return clone(data.evaluation);
  if(path==='/tickets'&&options.method==='POST'){
   const b=options.body;if(!b.customer||b.customer.length<2||!b.message||b.message.length<10||!b.subject||b.subject.length<3)throw Error('Проверьте обязательные поля обращения');
   const key=options.headers?.['Idempotency-Key'];const old=data.tickets.find(t=>t.idempotency_key===key&&key);if(old)return clone(old);
   const now=new Date().toISOString();const t={...clone(b),id:'SO-'+crypto.randomUUID().slice(0,8).toUpperCase(),status:'new',category:'other',revision:0,result:null,reviews:[],created_at:now,updated_at:now,idempotency_key:key};data.tickets.unshift(t);save();return clone(t);
  }
  const match=path.match(/^\/tickets\/([^/]+)(?:\/(analyze|review))?$/);if(!match)throw Error('Маршрут не найден');
  const t=data.tickets.find(t=>t.id===match[1]);if(!t)throw Error('Обращение не найдено');
  if(!match[2])return clone(t);
  if(match[2]==='analyze'){
   if(['review','approved','escalated'].includes(t.status))return clone(t);
   await new Promise(r=>setTimeout(r,550));t.result=simulate(t,options.body?.fault||'none');t.category=t.result.categories[0];t.status=t.result.reason?'manual':'review';t.revision++;t.updated_at=new Date().toISOString();save();return clone(t);
  }
  const b=options.body;if(t.revision!==b.revision||!['review','manual'].includes(t.status))throw Error('revision_conflict');
  if(b.decision==='approve'&&(t.status!=='review'||b.text.trim().length<10))throw Error('Нельзя подтвердить непроверенный ответ');
  t.status=b.decision==='approve'?'approved':'escalated';t.revision++;t.reviews=t.reviews||[];t.reviews.push({...clone(b),actor:'Сотрудник',created_at:new Date().toISOString(),id:crypto.randomUUID()});save();return clone(t);
 }
};
