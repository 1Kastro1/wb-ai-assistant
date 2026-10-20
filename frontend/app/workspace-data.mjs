import {normalizeReviewFilters} from './review-filters.mjs';
/** Load only the visible section. Keys are stable; URLs reflect current filters. */
export function workspaceResources({page='shell',settingsTab='connections',owner=false,days=0,productQuery=''}={}){
 const common=[['stores','/stores'],['actions','/actions'],['assistant','/settings/assistant'],['notifications','/notifications'],...(owner?[['security','/security']]:[])];
 const sections={
  'Ассистент':[['conversations','/conversations'],['voice','/voice/status'],['ai','/ollama'],['stats','/analytics'],['questionStats','/questions/stats']],
  'Главная':[['stats','/analytics'],['system','/system/status'],['quality','/quality-report'],['overview','/analytics/marketplaces?days='+days],['sync','/sync/status']],
  'Отзывы':[['jobs','/jobs'],['auto','/auto-replies/status']],
  'Вопросы':[['jobs','/jobs'],['questionStats','/questions/stats']],
  'Товары':[['products','/products/page?limit=60&q='+encodeURIComponent(productQuery)]],
  'Память':[['memory','/memory'],['products','/products/page?limit=60&q=']],
  'Уведомления':[],
  'Настройки':({connections:[['sync','/sync/status'],['jobs','/jobs'],['performance','/settings/performance']],answers:[['search','/settings/search'],['brands','/brands'],['memory','/memory'],['metrics','/generation/metrics']],voice:[['voice','/voice/status']],security:[],automation:[['schedule','/settings/schedule'],['auto','/auto-replies/status']],data:[['portable','/settings/portable-sync']],team:owner?[['users','/users'],['teamDashboard','/team/dashboard'],['telegram','/settings/telegram']]:[]})[settingsTab]||[]
 };
 return [...common,...(sections[page]||[])].filter(([key])=>owner||!['metrics','portable','search'].includes(key));
}

/** Coalesces concurrent reads, applies each result immediately, and never caches errors. */
export function createWorkspaceLoader(api,{ttl=15000,now=()=>Date.now()}={}){
 let cache=new Map(),generation=0;
 return {clear(){generation++;cache=new Map()},async load(resources,onValue,{force=false}={}){
  const epoch=generation;
  const results=await Promise.allSettled(resources.map(async([key,path])=>{
   let entry=cache.get(path);
   if(!entry?.promise&&(force||!entry||now()-entry.at>ttl))entry=undefined;
   if(!entry){entry={promise:null,at:0,value:null};const record=entry;record.promise=api(path,'GET',undefined,path.split('?')[0]==='/analytics'?45000:15000).then(value=>{record.value=value;record.at=now();record.promise=null;return value}).catch(error=>{if(cache.get(path)===record)cache.delete(path);throw error});cache.set(path,record)}
   const value=entry.promise?await entry.promise:entry.value;
   if(epoch===generation)onValue(key,value);
   return value;
  }));
  return results;
 }};
}

export function readViewPreferences(storage,key){
 try{const data=JSON.parse(storage?.getItem(key)||'{}');return {exists:Object.keys(data).length>0,reviewFilters:normalizeReviewFilters(data.reviewFilters),reviewLimit:Math.max(30,Math.min(100,Number(data.reviewLimit)||30)),filter:['all','positive','negative','rating_only','with_text','manual','context'].includes(data.filter)?data.filter:'all',query:typeof data.query==='string'?data.query.slice(0,200):'',productQuery:typeof data.productQuery==='string'?data.productQuery.slice(0,200):'',questionQuery:typeof data.questionQuery==='string'?data.questionQuery.slice(0,200):'',questionFilter:['all','unanswered','draft','answered'].includes(data.questionFilter)?data.questionFilter:'unanswered',questionKind:typeof data.questionKind==='string'?data.questionKind.slice(0,80):'',page:typeof data.page==='string'?data.page:'Ассистент',scroll:data.scroll&&typeof data.scroll==='object'?data.scroll:{}}}catch{return {filter:'all',query:'',productQuery:'',questionQuery:'',questionFilter:'unanswered',questionKind:'',page:'Ассистент',scroll:{}}}
}
