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
test('legacy schedules preserve both groups and payload contains no hidden service settings',()=>{
 const values=scheduleValues({enabled:true,reviews_hours:4,questions_hours:8,drafts_hours:12,drafts_enabled:true});
 assert.deepEqual(schedulePayload(values),{reviews_hours:4,questions_hours:8,drafts_hours:12,question_drafts_hours:12,reviews_sync_enabled:true,questions_sync_enabled:true,drafts_enabled:true,question_drafts_enabled:false});
 assert.equal(scheduleValues({enabled:false,drafts_enabled:true}).drafts_enabled,false);
 const separate=schedulePayload({...values,reviews_sync_enabled:false,question_drafts_enabled:true,question_drafts_hours:2});
 assert.equal(separate.questions_sync_enabled,true);assert.equal(separate.question_drafts_hours,2);assert.equal(separate.drafts_hours,12);
});
test('intervals reject empty values, fractions and values outside one week',()=>{
 const values=scheduleValues({enabled:false});
 for(const key of ['reviews_hours','questions_hours','drafts_hours','question_drafts_hours'])for(const value of ['',0,169,1.5,'abc'])assert.throws(()=>schedulePayload({...values,[key]:value}));
 assert.equal(schedulePayload({...values,reviews_hours:'3'}).reviews_hours,3);
});
test('reviews and questions each have independent update and generation controls',()=>{
 const Panel=component(path.resolve('app/AutomationSettings.tsx')).default;
 const markup=renderToStaticMarkup(React.createElement(Panel,{schedule:{enabled:false},onSave:async()=>{}}));
 assert.equal((markup.match(/type="number"/g)||[]).length,4);
 assert.equal((markup.match(/type="checkbox"/g)||[]).length,4);
 assert.match(markup,/aria-label="Отзывы"/);assert.match(markup,/aria-label="Вопросы"/);
 assert.match(markup,/Обновление данных/);assert.match(markup,/Генерация ответов/);
 assert.match(markup,/даже если закрыть сайт/);assert.match(markup,/после вашего подтверждения/);
 assert.doesNotMatch(markup,/Минимальное качество|Каждые N дней|Резервная копия, каждые/);
});
