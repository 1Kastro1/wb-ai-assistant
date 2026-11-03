export const workStages=[['sync','Обновление'],['generation','Ответы'],['verification','Проверка'],['publication','Выгрузка']];
export function workStage(task){
 if(['scheduled_publication','wb_publish_send','wb_publish_prepare'].includes(task.kind))return 'publication';
 if(['products','reviews','questions'].includes(task.kind))return 'sync';
 if(task.stage?.includes('Проверка')||task.kind==='draft_recheck'&&!task.label?.includes('Обновление ответов'))return 'verification';
 return 'generation';
}
export function serverWorkGroups(tasks,now=Date.now()/1000){
 const groups=new Map();
 for(const task of tasks){
  const key=task.store_id||'general';
  if(!groups.has(key))groups.set(key,{id:key,name:task.store_name||'Служебные процессы',provider:task.provider||'',tasks:[],stages:{}});
  const group=groups.get(key);group.tasks.push(task);
  const keyStage=workStage(task),stage=group.stages[keyStage]||{running:0,queued:0,retry:0};
  if(task.status==='running')stage.running++;
  else if(task.retry_at>now)stage.retry++;
  else stage.queued++;
  group.stages[keyStage]=stage;
 }
 return [...groups.values()].sort((a,b)=>a.provider.localeCompare(b.provider));
}
