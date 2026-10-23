import {test} from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),ts=require('typescript');
const source=fs.readFileSync(new URL('../app/client.ts',import.meta.url),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;

test('compact polling reuses unchanged status and clears it on session or mutation',async()=>{
 const module={exports:{}},calls=[];
 let response;
 const fetch=async(_url,options)=>{calls.push(options);return response};
 new Function('exports','fetch',js)(module.exports,fetch);
 const {api,setCsrf}=module.exports;
 const reply=(status,body='',etag='')=>({status,ok:status>=200&&status<300,text:async()=>body,headers:{get:()=>etag}});
 setCsrf('owner-session');response=reply(200,'[{"id":"job","status":"running"}]','"one"');
 const first=await api('/jobs?compact=true');
 response=reply(304);assert.equal(await api('/jobs?compact=true'),first);
 assert.equal(calls.at(-1).headers['If-None-Match'],'"one"');
 response=reply(200,'{}');await api('/jobs/job/cancel','POST',{});
 response=reply(200,'[]','"two"');await api('/jobs?compact=true');
 assert.equal(calls.at(-1).headers['If-None-Match'],undefined);
 setCsrf('another-session');await api('/jobs?compact=true');
 assert.equal(calls.at(-1).headers['If-None-Match'],undefined);
});
