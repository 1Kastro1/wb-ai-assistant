import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createWorkspaceLoader} from '../app/workspace-data.mjs';
import {activityGroups} from '../app/activity-groups.mjs';
import {draftState} from '../app/operations.mjs';

test('a failed refresh retains timestamped data but switching scope clears it',async()=>{
 let fail=false;const values=[];
 const loader=createWorkspaceLoader(async()=>{if(fail)throw Error('Unavailable');return {total:12}},{now:()=>123});
 const receive=(key,value,meta)=>values.push({key,value,meta});
 await loader.load([['stats','/analytics']],receive);
 fail=true;
 assert.equal((await loader.load([['stats','/analytics']],receive,{force:true}))[0].status,'rejected');
 assert.deepEqual(values.at(-1),{key:'stats',value:{total:12},meta:{at:123,stale:true}});
 loader.clear();values.length=0;
 await loader.load([['stats','/analytics']],receive);
 assert.equal(values.length,0);
});

test('bulk rule entries retain raw details and do not group errors or another actor',()=>{
 const row=(id,seconds,extra={})=>({id,user_id:'a',username:'a',timestamp:new Date(1700000000000-seconds*1000).toISOString(),status:200,details:{event:'rule_deleted',rule:'text '+id,scope:'global'},...extra});
 const groups=activityGroups([row(1,0),row(2,10),row(3,20,{status:500}),row(4,30,{user_id:'b'}),row(5,500)]);
 assert.equal(groups.length,4);assert.deepEqual(groups[0].entries.map(x=>x.id),[1,2]);
 assert.equal(groups[0].entries[1].details.rule,'text 2');
});

test('technical checks never claim a draft is ready and manual risk takes precedence',()=>{
 assert.match(draftState({has_draft:true,verification_state:'technical'}),/сервис проверки недоступен/);
 assert.equal(draftState({has_draft:true,verification_state:'technical',manual:true}),'Нужна личная проверка');
});
