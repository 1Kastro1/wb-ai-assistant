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
 assert.deepEqual(schedulePayload(values),{reviews_hours:4,questions_hours:8,drafts_hours:12,question_drafts_hours:12,reviews_sync_enabled:true,questions_sync_enabled:true,drafts_enabled:true,question_drafts_enabled:false,publication_enabled:false,publication_hours:1,publication_reviews:true,publication_questions:true,publication_low_ratings:false,publication_all_low_ratings:false,publication_markets:{wb:true,ozon:true,yandex:true}});
 assert.equal(scheduleValues({enabled:false,drafts_enabled:true}).drafts_enabled,false);
 const separate=schedulePayload({...values,reviews_sync_enabled:false,question_drafts_enabled:true,question_drafts_hours:2});
 assert.equal(separate.questions_sync_enabled,true);assert.equal(separate.question_drafts_hours,2);assert.equal(separate.drafts_hours,12);
});
test('intervals reject empty values, fractions and values outside one week',()=>{
 const values=scheduleValues({enabled:false});
 for(const key of ['reviews_hours','questions_hours','drafts_hours','question_drafts_hours','publication_hours'])for(const value of ['',0,169,1.5,'abc'])assert.throws(()=>schedulePayload({...values,[key]:value}));
 assert.equal(schedulePayload({...values,reviews_hours:'3'}).reviews_hours,3);
});
test('reviews and questions each have independent update and generation controls',()=>{
 const Panel=component(path.resolve('app/AutomationSettings.tsx')).default;
 const markup=renderToStaticMarkup(React.createElement(Panel,{schedule:{enabled:false},onSave:async()=>{}}));
 assert.equal((markup.match(/type="number"/g)||[]).length,5);
 assert.equal((markup.match(/type="checkbox"/g)||[]).length,8);
 assert.match(markup,/aria-label="Отзывы"/);assert.match(markup,/aria-label="Вопросы"/);
 assert.match(markup,/Обновление данных/);assert.match(markup,/Генерация ответов/);
 assert.match(markup,/даже если закрыть сайт/);assert.match(markup,/после вашего подтверждения/);
 assert.doesNotMatch(markup,/Минимальное качество|Каждые N дней|Резервная копия, каждые/);
});

 test('automatic publication requires an explicit switch and an answer type',()=>{
  const values=scheduleValues({enabled:true,publication_enabled:true,publication_hours:3});
  assert.equal(schedulePayload(values).publication_hours,3);
  assert.throws(()=>schedulePayload({...values,publication_reviews:false,publication_questions:false}),/Выберите ответы/);
  const Panel=component(path.resolve('app/AutomationSettings.tsx')).default;
  const html=renderToStaticMarkup(React.createElement(Panel,{schedule:{enabled:true,publication_enabled:false,publication_runtime:{available:true,stores:[{id:'a',provider:'wb',name:'WB',connected:true},{id:'b',provider:'ozon',name:'Ozon',connected:true},{id:'c',provider:'yandex',name:'Яндекс',connected:true}]}},onSave:async()=>{}}));
  assert.match(html,/Автоматическая выгрузка/);assert.match(html,/публиковаться покупателям автоматически/);
  assert.match(html,/Wildberries/);assert.match(html,/Ozon/);assert.match(html,/Яндекс Маркет/);
  assert.match(html,/Выгрузка выключена/);assert.match(html,/по Москве/);
 });

test('market switches keep their saved scope and communicate deferred saving',()=>{
 const saved={enabled:true,publication_enabled:true,publication_markets:{wb:true,ozon:false,yandex:true}};
 const values=scheduleValues(saved);
 assert.equal(schedulePayload(values).publication_markets.ozon,false);
 assert.deepEqual(scheduleValues({enabled:true}).publication_markets,{wb:true,ozon:true,yandex:true});
 const Panel=component(path.resolve('app/AutomationSettings.tsx')).default;
 const html=renderToStaticMarkup(React.createElement(Panel,{schedule:{...saved,publication_runtime:{available:true,stores:[{id:'wb',provider:'wb',name:'Мой WB',enabled:true,connected:true},{id:'oz',provider:'ozon',name:'Мой Ozon',enabled:false,connected:true},{id:'ya',provider:'yandex',name:'Мой Яндекс',enabled:true,connected:true}]}},onSave:async()=>{}}));
 assert.match(html,/aria-label="Автоматическая выгрузка: Wildberries"/);
 assert.match(html,/aria-label="Автоматическая выгрузка: Ozon"/);
 assert.match(html,/aria-label="Автоматическая выгрузка: Яндекс Маркет"/);
 assert.match(html,/Выключено для маркета/);assert.match(html,/применяются после сохранения/);
});

test('sidebar separates connection from publication and groups navigation',()=>{
 const Sidebar=component(path.resolve('app/AppSidebar.tsx')).default;
 const html=renderToStaticMarkup(React.createElement(Sidebar,{user:{display_name:'Владелец'},stores:{active:'wb',items:[{id:'wb',name:'Магазин',provider:'wb',connected:true,publication_state:'disabled'}]},page:'Главная',nav:[['','Главная'],['','История действий'],['','Память'],['','Настройки']],pending:0,unread:0,busy:false,onPage:()=>{},onStore:()=>{},onLogout:()=>{}}));
 assert.match(html,/API подключён/);assert.match(html,/Выгрузка выключена/);
 assert.match(html,/КОНТРОЛЬ/);assert.match(html,/УПРАВЛЕНИЕ/);assert.match(html,/aria-current="page"/);
});

 test('low ratings are opt-in and valid as the only publication scope',()=>{
 const values=scheduleValues({enabled:true,publication_enabled:true});
 assert.equal(values.publication_low_ratings,false);
 assert.equal(schedulePayload({...values,publication_reviews:false,publication_questions:false,publication_low_ratings:true}).publication_low_ratings,true);
 const Header=component(path.resolve('app/SettingsHeader.tsx')).default;
 const html=renderToStaticMarkup(React.createElement(Header,{active:'automation',onChange:()=>{},owner:true}));
 assert.equal((html.match(/aria-current="page"/g)||[]).length,1);
 const member=renderToStaticMarkup(React.createElement(Header,{active:'answers',onChange:()=>{},owner:false}));
 assert.doesNotMatch(member,/Безопасность|Автоматизация|Команда/);
});

 test('all verified low-rating replies require a separate persisted choice',()=>{
 const values=scheduleValues({enabled:true,publication_low_ratings:true});
 assert.equal(values.publication_all_low_ratings,false);
 assert.equal(schedulePayload({...values,publication_all_low_ratings:true}).publication_all_low_ratings,true);
 const Panel=component(path.resolve('app/AutomationSettings.tsx')).default;
 const html=renderToStaticMarkup(React.createElement(Panel,{schedule:{enabled:true,publication_low_ratings:true,publication_all_low_ratings:true},onSave:async()=>{}}));
 assert.match(html,/Режим отправки отзывов 1–3★/);
 assert.match(html,/Все ответы, прошедшие проверку/);
 assert.match(html,/Опасные ситуации остаются для личной проверки/);
 });
