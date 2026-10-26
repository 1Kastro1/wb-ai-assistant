import {test} from 'node:test';
import assert from 'node:assert/strict';
import {draftState,notificationGroups} from '../app/operations.mjs';
test('a saved draft is never presented as verified without a passed verdict',()=>{
 assert.equal(draftState({has_draft:true,manual:true,status:'manual_review',verification_passed:true}),'Нужна личная проверка');
 assert.equal(draftState({has_draft:true,status:'draft_ready',verification_passed:false}),'Черновик сохранён · нужна проверка');
 assert.equal(draftState({has_draft:true,status:'draft_ready',verification_passed:true}),'Проверен · ожидает подтверждения');
});
test('notification repetitions group only within one store and retain all events',()=>{
 const groups=notificationGroups([{id:1,store_id:'a',kind:'job_failed',severity:'error',created_at:'2026-01-01',read:true},{id:2,store_id:'b',kind:'job_failed',severity:'error',created_at:'2026-01-02',read:false},{id:3,store_id:'a',kind:'job_failed',severity:'error',created_at:'2026-01-03',read:false}]);
 assert.equal(groups.length,2);assert.equal(groups[0].count,2);assert.equal(groups[0].latest.id,3);assert.equal(groups[0].unread,1);assert.equal(groups[0].events.length,2);
});
