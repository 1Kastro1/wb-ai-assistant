import {test} from 'node:test';
import assert from 'node:assert/strict';
import {serverWorkGroups,workStages} from '../app/server-work.mjs';

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
