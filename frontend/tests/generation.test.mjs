import assert from 'node:assert/strict';
import {test} from 'node:test';
const {api}=await import('../app/client.ts');

test('network timeout aborts both fetch and response body reads',async()=>{
  const originalFetch=globalThis.fetch;
  try{
    for(const stalledBody of [false,true]){
      let signal;
      globalThis.fetch=async(_url,options)=>{
        signal=options.signal;
        const stalled=()=>new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}));
        return stalledBody?{ok:true,text:stalled}:stalled();
      };
      await assert.rejects(api('/jobs/job','GET',undefined,10),/Сервер не ответил вовремя/);
      assert.equal(signal.aborted,true);
    }
  }finally{globalThis.fetch=originalFetch;}
});

test('invalid successful JSON is an error rather than a phantom queued job',async()=>{
  const originalFetch=globalThis.fetch;
  try{
    globalThis.fetch=async()=>({ok:true,text:async()=>'<html>proxy error</html>'});
    await assert.rejects(api('/jobs/job'),/некорректный ответ/);
  }finally{globalThis.fetch=originalFetch;}
});
