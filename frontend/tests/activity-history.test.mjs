import {scopeLabel} from '../app/presentation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {activityDescription} from '../app/activity-labels.mjs';

test('errors are never described as successful actions',()=>{
 assert.equal(activityDescription({path:'/settings/wb-test',status:500}),'Ошибка выполнения действия');
 assert.equal(activityDescription({status:500,details:{event:'job_failed',job_kind:'products'}}),'Синхронизация завершилась с ошибкой');
 assert.equal(activityDescription({status:500,details:{event:'job_failed',job_kind:'drafts'}}),'Подготовка ответов завершилась с ошибкой');
});
const require=createRequire(import.meta.url),fs=require('node:fs'),path=require('node:path'),ts=require('typescript'),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../app');
function loadComponent(name){const file=path.resolve(root,name+'.tsx'),source=fs.readFileSync(file,'utf8'),module={exports:{}};const code=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS}}).outputText;const localRequire=name=>name==='./presentation.mjs'?{scopeLabel}:name==='./activity-labels.mjs'?{activityDescription}:name==='./client'?{api(){throw new Error('SSR must not request data')}}:name.startsWith('.')?loadComponent(name.slice(2)):require(name);new Function('require','module','exports',code)(localRequire,module,module.exports);return module.exports}
test('history distinguishes proposed and activated rules',()=>{assert.equal(activityDescription({details:{event:'rule_proposed'}}),'Предложено новое правило');assert.equal(activityDescription({details:{event:'rule_added'}}),'Правило добавлено в память');assert.equal(activityDescription({details:{event:'rule_deleted'}}),'Правило удалено из памяти')});
test('legacy entries do not invent a rule or claim it was activated',()=>{assert.equal(activityDescription({path:'/memory/propose'}),'Предложено новое правило')});
test('reply editing and feedback have distinct descriptions',()=>{assert.notEqual(activityDescription({path:'/drafts/d',method:'PATCH'}),activityDescription({path:'/drafts/d/feedback',method:'POST'}))});
test('timeline shows author and safely renders the exact rule',()=>{const Rows=loadComponent('ActivityHistory').ActivityRows;const html=renderToStaticMarkup(React.createElement(Rows,{items:[{id:1,actor_name:'Камиль',username:'owner',timestamp:'2026-10-14T09:00:00Z',details:{event:'rule_added',rule:'Не пиши <script>alert(1)</script>',scope:'global'}}]}));assert(html.includes('Камиль'));assert(html.includes('@owner'));assert(html.includes('Правило добавлено в память'));assert(html.includes('&lt;script&gt;'));assert(!html.includes('<script>'))});
test('removed menu tabs are replaced by activity history',()=>{const source=fs.readFileSync(path.join(root,'page.tsx'),'utf8');const nav=new Function('return '+source.match(/const nav = (.*);/)[1])();const Sidebar=loadComponent('AppSidebar').default;const html=renderToStaticMarkup(React.createElement(Sidebar,{user:{display_name:'Владелец'},stores:{items:[]},page:'История действий',nav,pending:0,unread:0,busy:false,onPage(){},onStore(){},onLogout(){}}));assert(html.includes('История действий'));assert(!html.includes('aria-label="Задачи"'));assert(!html.includes('aria-label="Действия"'));assert(!source.includes("setPage('Действия')"))});
test('confirmation dialog keeps errors and explicit confirmation visible',()=>{const Dialog=loadComponent('ApprovalDialog').default;const html=renderToStaticMarkup(React.createElement(Dialog,{busy:false,error:'Черновик изменён',onClose(){}},React.createElement('button',null,'Подтвердить')));assert(html.includes('role="dialog"'));assert(html.includes('role="alert"'));assert(html.includes('Черновик изменён'));assert(html.includes('Подтвердить'));assert(html.includes('Закрыть'))});

test('the complete page remains valid JSX after removing sections',()=>{const source=fs.readFileSync(path.join(root,'page.tsx'),'utf8');const result=ts.transpileModule(source,{reportDiagnostics:true,fileName:'page.tsx',compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS}});assert.deepEqual((result.diagnostics||[]).filter(d=>d.category===ts.DiagnosticCategory.Error).map(d=>ts.flattenDiagnosticMessageText(d.messageText,' ')),[])});
