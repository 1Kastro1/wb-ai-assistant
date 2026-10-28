import {test} from 'node:test';
import assert from 'node:assert/strict';
import {batchProgressJob,batchProgress,batchWaitReason,batchStage} from '../app/batch-progress.mjs';
const job=(id,status,result={})=>({id,kind:'drafts',status,result:{account_id:'shop',...result}});

test('queued batches explain active work and retries without stale running rows',()=>{
 assert.match(batchWaitReason(job('one','queued'),[{id:'other',kind:'question_drafts',status:'running'}]),/ответы на вопросы/);
 assert.match(batchWaitReason(job('one','queued',{retry_at:100}),[],1000),/Повтор после временной ошибки/);
 assert.match(batchWaitReason(job('one','queued'),[]),/Повторно запускать.*не нужно/);
 assert.equal(batchWaitReason(job('one','running')), '');
});

test('a queued page selection cannot hide the running all-store job',()=>{
 const selected=job('selected','queued',{selected_ids:['one'],total:30});
 const full=job('full','running',{processed:33,total:15000});
 assert.equal(batchProgressJob([selected,full],'shop',selected),full);
});
test('progress restores after closing the browser without storage or matching creator',()=>{
 const full=job('full','running',{requested_by:'another-operator',manual_batch:true});
 assert.equal(batchProgressJob([full],'shop'),full);
 assert.equal(batchProgressJob([full],'another-shop',full),null);
});
test('selected and date-limited jobs are not mistaken for full-store runs',()=>{
 const selected=job('selected','queued',{selected_ids:['one']});
 const recent=job('recent','running',{since:'2026-10-01'});
 const full=job('full','queued');
 assert.equal(batchProgressJob([recent,selected,full],'shop'),full);
});
test('saved completed progress survives a new session and counts manual attention',()=>{
 const finished=job('finished','completed',{manual_batch:true,total:100,processed:92,failed:8});
 assert.equal(batchProgressJob([finished],'shop'),finished);
 assert.deepEqual(batchProgress(finished),{processed:92,failed:8,skipped:0,attention:0,total:100,attempted:100,percent:100,remaining:0});
 assert.equal(batchProgress(job('running','running',{total:15631,processed:33,failed:1})).attempted,34);
 assert.equal(batchProgress(job('queued','queued',{total:30})).percent,0);
});

test('fractional progress separates skipped, manual checks and technical errors',()=>{
 const progress=batchProgress(job('full','running',{total:10487,processed:41,failed:2,skipped:49,attention:3,manual_errors:1}));
 assert.equal(progress.percent,0.9);
 assert.equal(progress.attempted,93);
 assert.equal(progress.remaining,10394);
 assert.equal(progress.attention,3);
 assert.equal(progress.failed,2);
});

test('full counters do not announce completion before server completes the job',()=>{
 assert.equal(batchStage(job('one','running',{total:44,processed:44})), 'Завершаем подготовку');
 assert.equal(batchStage(job('one','running',{total:44,processed:44,retrying:1})), 'Ожидаем повторной обработки');
 assert.equal(batchStage(job('one','completed',{total:44,processed:44})), 'Обработка завершена');
 assert.equal(batchStage(job('one','running',{total:44,processed:20})), 'Готовим и проверяем ответы');
 assert.equal(batchStage({...job('one','running'),cancel_requested:true}), 'Останавливаем подготовку');
 assert.equal(batchStage(job('one','failed')), 'Не удалось завершить подготовку');
 assert.equal(batchStage(job('one','interrupted')), 'Подготовка прервана');
 assert.equal(batchStage(job('one','cancelled')), 'Подготовка остановлена');
});
