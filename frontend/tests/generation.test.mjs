import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {stripTypeScriptTypes} from 'node:module';
import {test} from 'node:test';

const source=await readFile(new URL('../app/page.tsx',import.meta.url),'utf8');
const start=source.indexOf('async function singleDraftJob(');
const end=source.indexOf('\nconst nav',start);
const jobSource=stripTypeScriptTypes('type Row = Record<string, any>;\n'+source.slice(start,end));

function jobRunner(api){
  let elapsed=0;
  return new Function('api','performance','setTimeout',jobSource+'\nreturn singleDraftJob;')(
    api,{now:()=>elapsed},(callback,delay)=>{elapsed+=delay;callback();},
  );
}

test('completed draft returns to the card',async()=>{
  const draft={id:'draft',text:'Спасибо за оценку'};
  const run=jobRunner(async()=>({status:'completed',result:{draft}}));
  assert.equal(await run('review',''),draft);
});

test('failed job exposes the server error',async()=>{
  const run=jobRunner(async()=>({status:'failed',result:{message:'Модель недоступна'}}));
  await assert.rejects(run('review',''),/Модель недоступна/);
});

test('queued or running jobs release the card after five minutes',async()=>{
  for(const status of ['queued','running']){
    let reads=0;
    const run=jobRunner(async(path,method,body,timeout)=>{
      assert.equal(timeout,15000);
      if(method==='GET')reads++;
      return {id:'job',status};
    });
    await assert.rejects(run('review',''),/больше пяти минут.*Задачи/);
    assert.equal(reads,150);
  }
});

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
