import {test} from 'node:test';
import assert from 'node:assert/strict';
import {serverWorkGroups,workStages,stageStatus,marketWorkStatus,minionStatus,lastMovement,relativeTime} from '../app/server-work.mjs';

test('market summary and minion labels distinguish idle, stopped and active work',()=>{
 assert.equal(marketWorkStatus({stages:{}}).label,'Новых задач нет');
 assert.equal(marketWorkStatus({stages:{},enabled:{sync:false,generation:false,verification:false,publication:false}}).label,'На паузе');
 assert.equal(marketWorkStatus({stages:{},minion:{status:'failed'}}).label,'Нужна помощь');
 assert.equal(marketWorkStatus({stages:{verification:{running:1}}}).label,'Выполняется');
 assert.equal(minionStatus({status:'queued',wait_reason:'cadence',message:'Миньон проверяет'}),'Ожидает следующего прохода');
});

test('idle stores stay visible; completion, disabled and backoff are distinct',()=>{
 const [group]=serverWorkGroups([{store_id:'wb',kind:'drafts',status:'queued',retry_at:220}],100,
  [{id:'wb',provider:'wb',counts:{published:12},enabled:{publication:false},completed_stages:{sync:{finished_at:'2026-11-03T12:00:00Z'}}}]);
 assert.equal(stageStatus(group,'generation',100).label,'Повтор через 2 мин.');
 assert.equal(stageStatus(group,'sync',100).label,'Завершено');
 assert.equal(stageStatus(group,'publication',100).label,'Выключено');
 assert.equal(group.counts.published,12);
 assert.equal(serverWorkGroups([],100,[{id:'idle',provider:'ozon'}]).length,1);
 assert.equal(lastMovement(group),'2026-11-03T12:00:00Z');
 assert.equal(relativeTime(null),'ожидаем первый результат');
});

test('a hundred verification requests remain three marketplace processes',()=>{
 const tasks=Array.from({length:102},(_,i)=>({id:String(i),store_id:['wb','ya','oz'][i%3],provider:['wb','yandex','ozon'][i%3],store_name:'Магазин',kind:'draft_recheck',label:'Проверка правил',status:i===0?'running':'queued',retry_at:i===5?200:0}));
 const groups=serverWorkGroups(tasks,100);
 assert.equal(groups.length,3);
 assert.equal(groups.reduce((sum,g)=>sum+g.tasks.length,0),102);
 assert.equal(groups.find(g=>g.id==='wb').stages.verification.running,1);
 assert.equal(groups.find(g=>g.id==='oz').stages.verification.retry,1);
 assert.equal(workStages.length,4);
});

test('generation, checking and publication are separate stages without invented completion',()=>{
 const [group]=serverWorkGroups([
  {store_id:'wb',kind:'draft_recheck',label:'Обновление ответов по правилам',status:'queued'},
  {store_id:'wb',kind:'scheduled_publication',status:'running'},
  {store_id:'wb',kind:'reviews',status:'queued'},
 ]);
 assert.equal(group.stages.generation.queued,1);
 assert.equal(group.stages.publication.running,1);
 assert.equal(group.stages.sync.queued,1);
 assert.equal(group.stages.verification,undefined);
 assert.deepEqual(serverWorkGroups([]),[]);
});

test('committed reply progress wins over an older completed whole job',()=>{
 assert.equal(lastMovement({last_progress_at:'2026-11-03T17:55:00Z',tasks:[],completed_stages:{sync:{finished_at:'2026-11-03T10:00:00Z'}}}),'2026-11-03T17:55:00Z');
});


test('ages queued work per stage from its own last turn', () => {
 const now=Date.parse('2026-11-26T12:30:00Z')/1000;
 const [group]=serverWorkGroups([
  {status:'queued',store_id:'oz',stage_key:'publication',created_at:'2026-11-26T10:00:00Z',last_served_at:'2026-11-26T12:12:00Z'},
  {status:'queued',store_id:'oz',stage_key:'verification',created_at:'2026-11-26T12:29:00Z'}
 ],now);
 assert.equal(stageStatus(group,'publication',now).label,'В очереди 18 мин.');
 assert.equal(stageStatus(group,'verification',now).label,'Ожидает очереди');
});
