import {test} from 'node:test';
import assert from 'node:assert/strict';
import {scheduleValues,schedulePayload} from '../app/automation-settings.mjs';
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
function component(file){
 const compiled=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const module={exports:{}};
 const localRequire=name=>name.endsWith('.css')?{}:!name.startsWith('.')?require(name):component([name,name+'.tsx',name+'.ts'].map(value=>path.resolve(path.dirname(file),value)).find(value=>fs.existsSync(value)));
 new Function('require','module','exports',compiled)(localRequire,module,module.exports);return module.exports;
}
test('legacy schedules map to two controls and payload contains no hidden service settings',()=>{
 const values=scheduleValues({enabled:true,reviews_hours:4,questions_hours:8,drafts_hours:12,drafts_enabled:true});
 assert.deepEqual(schedulePayload(values),{sync_enabled:true,generation_enabled:true,sync_hours:4,drafts_hours:12});
 assert.equal(scheduleValues({enabled:false,drafts_enabled:true}).generation_enabled,false);
});
test('intervals reject empty values, fractions and values outside one week',()=>{
 for(const value of ['',0,169,1.5,'abc'])assert.throws(()=>schedulePayload({sync_hours:value,drafts_hours:6}));
 assert.equal(schedulePayload({sync_hours:'3',drafts_hours:'6'}).sync_hours,3);
});
test('automation has exactly two timing fields and explains server execution and draft approval',()=>{
 const Panel=component(path.resolve('app/AutomationSettings.tsx')).default;
 const markup=renderToStaticMarkup(React.createElement(Panel,{schedule:{enabled:false},onSave:async()=>{}}));
 assert.equal((markup.match(/type="number"/g)||[]).length,2);
 assert.equal((markup.match(/type="checkbox"/g)||[]).length,2);
 assert.match(markup,/Обновление данных/);assert.match(markup,/Генерация ответов/);
 assert.match(markup,/даже если закрыть сайт/);assert.match(markup,/после вашего подтверждения/);
 assert.doesNotMatch(markup,/Минимальное качество|Каждые N дней|Резервная копия, каждые/);
});
