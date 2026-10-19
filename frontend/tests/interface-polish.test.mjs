import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createButtonAction,savedDensity} from '../app/button-action.mjs';

test('button blocks double clicks until the request finishes',async()=>{
 let resolve,calls=0;const states=[];
 const execute=createButtonAction(state=>states.push(state));
 const pending=execute(()=>{calls++;return new Promise(done=>{resolve=done})});
 assert.equal(await execute(async()=>{calls++;return true}),false);
 assert.deepEqual(states,['pending']);assert.equal(calls,1);
 resolve(true);assert.equal(await pending,true);assert.deepEqual(states,['pending','success']);
});
test('handled errors never show success and allow a real retry',async()=>{
 const states=[],execute=createButtonAction(state=>states.push(state));
 assert.equal(await execute(async()=>false),false);
 assert.deepEqual(states,['pending','error']);
 assert.equal(await execute(async()=>true),true);
 assert.deepEqual(states,['pending','error','pending','success']);
});
test('unexpected errors do not leave a button permanently busy',async()=>{
 const states=[],execute=createButtonAction(state=>states.push(state));
 assert.equal(await execute(async()=>{throw Error('Offline')}),false);
 assert.equal(await execute(async()=>true),true);
 assert.deepEqual(states,['pending','error','pending','success']);
});
test('density preference is scoped and tolerates unavailable browser storage',()=>{
 const storage={getItem:key=>key==='user-one'?'compact':null};
 assert.equal(savedDensity(storage,'user-one'),'compact');
 assert.equal(savedDensity(storage,'user-two'),'comfortable');
 assert.equal(savedDensity({getItem(){throw Error('Blocked')}},'user-one'),'comfortable');
 assert.equal(savedDensity({getItem:()=>'<invalid>'},'user-one'),'comfortable');
});
