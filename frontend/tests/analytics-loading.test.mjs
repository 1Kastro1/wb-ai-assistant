import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createWorkspaceLoader} from '../app/workspace-data.mjs';

test('a 17.5 second analytics calculation can complete without delaying fast cards',async()=>{
 let finish; const slow=new Promise(resolve=>{finish=resolve}); const events=[],budgets=[];
 const loader=createWorkspaceLoader(async(path,method,body,timeout)=>{
  budgets.push([path,timeout]);
  if(path.startsWith('/analytics?')){if(timeout<=17500)throw new Error('Request timed out');return slow;}
  return {ok:true};
 });
 const pending=loader.load([['stats','/analytics?days=30'],['system','/system/status']],(key,value)=>events.push([key,value]));
 await new Promise(resolve=>setImmediate(resolve));
 assert.deepEqual(events,[['system',{ok:true}]]);
 assert.equal(budgets.find(([path])=>path==='/system/status')[1],15000);
 finish({total:221679,unanswered:183388});
 const results=await pending;
 assert(results.every(result=>result.status==='fulfilled'));
 assert.deepEqual(events[1],['stats',{total:221679,unanswered:183388}]);
});

test('a failed analytics request remains retryable and is never converted to empty statistics',async()=>{
 let calls=0;const values=[];
 const loader=createWorkspaceLoader(async(path,method,body,timeout)=>{
  assert(timeout>17500&&timeout<=60000);
  if(++calls===1)throw new Error('Unavailable');return {total:12};
 });
 assert.equal((await loader.load([['stats','/analytics']],(_,value)=>values.push(value)))[0].status,'rejected');
 assert.deepEqual(values,[]);
 await loader.load([['stats','/analytics']],(_,value)=>values.push(value));
 assert.deepEqual(values,[{total:12}]);
});
