export const workStages=[['sync','Обновление'],['generation','Ответы'],['verification','Проверка'],['publication','Выгрузка']];
export function workStage(task){
 if(task.stage_key)return task.stage_key;
 if(['scheduled_publication','wb_publish_send','wb_publish_prepare','publication_reconcile'].includes(task.kind))return 'publication';
 if(['products','reviews','questions'].includes(task.kind))return 'sync';
 if(task.stage?.includes('Проверка')||task.kind==='draft_recheck'&&!task.label?.includes('Обновление ответов'))return 'verification';
 return 'generation';
}
export function serverWorkGroups(tasks,now=Date.now()/1000,processes=[]){
 const groups=new Map(processes.map(p=>[p.id,{...p,tasks:[],stages:{}}]));
 for(const task of tasks){
  const key=task.store_id||'general';
  if(!groups.has(key))groups.set(key,{id:key,name:task.store_name||'Служебные процессы',provider:task.provider||'',tasks:[],stages:{}});
  const group=groups.get(key);group.tasks.push(task);
  const keyStage=workStage(task),stage=group.stages[keyStage]||{running:0,queued:0,retry:0,retry_at:0};
  if(task.status==='running')stage.running++;
  else if(task.retry_at>now&&(task.retry_pending??true)){stage.retry++;stage.retry_at=stage.retry_at?Math.min(stage.retry_at,task.retry_at):task.retry_at;}
  else if(task.retry_at>now&&['idle','disabled'].includes(task.wait_reason))continue;
  else stage.queued++;
  group.stages[keyStage]=stage;
 }
 return [...groups.values()].sort((a,b)=>a.provider.localeCompare(b.provider));
}

export function stageStatus(group,key,now=Date.now()/1000){
 const s=group.stages[key];
 if(s?.running)return {label:'Выполняется',tone:'running'};
 if(s?.queued)return {label:'Ожидает очереди',tone:'waiting'};
 if(s?.retry)return {label:'Повтор через '+Math.max(1,Math.ceil((s.retry_at-now)/60))+' мин.',tone:'retry'};
 if(group.enabled?.[key]===false)return {label:'Выключено',tone:'idle'};
 if(group.completed_stages?.[key])return {label:'Завершено',tone:'done'};
 return {label:key==='verification'?'Нет ответов на проверке':'Новых задач нет',tone:'idle'};
}
export function marketWorkStatus(group,now=Date.now()/1000){
 const stages=workStages.map(([key])=>stageStatus(group,key,now));
 if(stages.some(s=>s.tone==='running'))return {label:'Выполняется',tone:'running'};
 if(group.minion?.status==='failed')return {label:'Нужна помощь',tone:'retry'};
 if(stages.some(s=>s.tone==='waiting'))return {label:'Ожидает очереди',tone:'waiting'};
 const retry=stages.find(s=>s.tone==='retry');
 if(retry)return retry;
 if(workStages.every(([key])=>group.enabled?.[key]===false))return {label:'На паузе',tone:'idle'};
 if(stages.some(s=>s.tone==='done'))return {label:'Последний проход завершён',tone:'done'};
 return {label:'Новых задач нет',tone:'idle'};
}

export function minionStatus(minion){
 if(minion.status==='failed')return 'Нужна проверка сервиса';
 if(minion.status==='cancelled')return 'Остановлен';
 if(minion.status==='running')return 'Проверяет отзыв';
 if(minion.wait_reason==='disabled')return 'На паузе';
 if(minion.wait_reason==='idle')return 'Ожидает сложных отзывов';
 return 'Ожидает следующего прохода';
}
export function lastMovement(group){
 const values=[group.last_progress_at,group.refresh?.last_progress_at,...group.tasks.map(t=>t.last_progress_at),
  ...Object.values(group.completed_stages||{}).map(s=>s.finished_at)].filter(Boolean);
 return values.sort().at(-1)||null;
}
export function relativeTime(value,now=Date.now()){
 const diff=(now-Date.parse(value))/60000;
 if(!Number.isFinite(diff))return 'ожидаем первый результат';
 if(diff<1)return 'только что';
 if(diff<60)return Math.floor(diff)+' мин. назад';
 if(diff<1440)return Math.floor(diff/60)+' ч. назад';
 return Math.floor(diff/1440)+' дн. назад';
}
