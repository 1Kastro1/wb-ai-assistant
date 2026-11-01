import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../app/page.tsx',import.meta.url),'utf8');
const method=source.slice(source.indexOf('async function findReviews('),source.indexOf('async function findProducts('));
function setup(){
 const requests=[],state={items:[{id:'old-unfiltered'}],error:'',loading:false,page:{total:100}};
 const env={scrollReady:{current:true},reviewResultsKey:{current:''},reviewSearchRequest:{current:0},currentUser:{id:'u'},stores:{active:'shop'},
  filter:'all',reviewFilters:{},query:'',viewKey:'u:shop',reviews:state.items,context:{},reviewViews:{current:new Map()},viewPreferences:{current:{}},
  reviewFilterParams:()=>new URLSearchParams(),emptyReviewFilters:()=>({}),sessionStorage:{setItem(){}},
  setReviewLoading:v=>state.loading=v,setReviewLoadError:v=>state.error=v,setReviews:v=>state.items=v,
  setReviewPage:v=>state.page=v,setSelected(){},setReviewCounts(){},
  api:path=>path.startsWith('/reviews/counts')?Promise.resolve({}):new Promise((resolve,reject)=>requests.push({resolve,reject}))};
 const load=new Function('env','with(env){'+method+';return findReviews;}')(env);
 return {load,requests,state};
}
test('a failed filter never shows the previous unfiltered reviews or a false empty result',async()=>{
 const {load,requests,state}=setup();
 const task=load(0,false,'manual');
 assert.deepEqual(state.items,[]);
 assert.equal(state.loading,true);
 requests[0].reject(new Error('Сервер не ответил вовремя'));
 await assert.rejects(task,/Сервер не ответил/);
 assert.deepEqual(state.items,[]);
 assert.equal(state.page.has_more,false);
 assert.match(state.error,/Сервер не ответил/);
 assert.equal(state.loading,false);
});
test('late failed requests cannot overwrite results of the newer filter',async()=>{
 const {load,requests,state}=setup();
 const first=load(0,false,'manual'),second=load(0,false,'positive');
 requests[1].resolve({items:[{id:'positive'}],total:1,has_more:false});await second;
 requests[0].reject(new Error('old failure'));await assert.rejects(first);
 assert.deepEqual(state.items,[{id:'positive'}]);assert.equal(state.error,'');
});
test('failed pagination preserves the already loaded matching reviews',async()=>{
 const {load,requests,state}=setup();
 const first=load();requests[0].resolve({items:[{id:'matching'}],total:60,has_more:true});await first;
 const more=load(30,true);requests[1].reject(new Error('network'));await assert.rejects(more);
 assert.deepEqual(state.items,[{id:'matching'}]);assert.equal(state.error,'network');
});
test('refreshing the same filter preserves matching cards while data loads',async()=>{
 const {load,requests,state}=setup();
 const first=load();requests[0].resolve({items:[{id:'matching'}],total:1,has_more:false});await first;
 const refresh=load();assert.deepEqual(state.items,[{id:'matching'}]);
 requests[1].resolve({items:[{id:'updated'}],total:1,has_more:false});await refresh;
 assert.deepEqual(state.items,[{id:'updated'}]);
});
