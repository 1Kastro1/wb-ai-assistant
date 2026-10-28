import {ruleTitle} from '../app/presentation.mjs';
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url),fs=require('node:fs'),path=require('node:path'),ts=require('typescript'),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const file=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../app/MemoryRules.tsx');
const module={exports:{}};
const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS}}).outputText;
new Function('require','module','exports',code)(name=>name==='./presentation.mjs'?{ruleTitle}:name==='./client'?{api(){throw new Error('SSR must not send requests')}}:require(name),module,module.exports);
const Rules=module.exports.default;
const items=[{id:'r',text:'REVIEW_ONLY_CONTENT',kind:'REVIEW_RULE',scope:'global',enabled:true},{id:'q',text:'UNIQOM_QUESTION_ONLY_CONTENT',kind:'QUESTION_RULE',scope:'global',enabled:true}];
const render=initialKind=>renderToStaticMarkup(React.createElement(Rules,{items,products:[],owner:true,initialKind,onRefresh(){},onPropose(){},onDelete(){}}));

test('review section displays only review rules and a rated review example',()=>{
 const html=render('REVIEW_RULE');
 assert(html.includes('REVIEW_ONLY_CONTENT'));
 assert(!html.includes('UNIQOM_QUESTION_ONLY_CONTENT'));
 assert(html.includes('Новое правило для отзывов'));
 assert(html.includes('Проверить на примере отзыва'));
 assert(html.includes('Оценка'));
 assert(html.includes('role="tablist"'));
 assert(html.includes('Создать правило'));
});
test('question section displays Uniqom rule and a question example without stars',()=>{
 const html=render('QUESTION_RULE');
 assert(html.includes('UNIQOM_QUESTION_ONLY_CONTENT'));
 assert(!html.includes('REVIEW_ONLY_CONTENT'));
 assert(html.includes('Новое правило для вопросов'));
 assert(html.includes('Вопрос покупателя'));
 assert(!html.includes('Оценка'));
 assert(!html.includes(' ★'));
 assert(html.includes('новый поиск в Uniqom в этом примере не запускается'));
});
test('enabled legacy common rules remain visible and explicitly labelled',()=>{
 const html=renderToStaticMarkup(React.createElement(Rules,{items:[{id:'legacy',text:'Старое правило',kind:'GLOBAL_RULE',scope:'brand:EIKOSHA',enabled:true}],products:[],owner:false,onRefresh(){},onPropose(){},onDelete(){}}));
 assert(html.includes('Старое правило'));
 assert(html.includes('Отзывы и вопросы (старое правило)'));
 assert(html.includes('EIKOSHA'));
 assert(html.includes('disabled'));
 assert(html.includes('Предложить сохранение'));
});

test('disabled historical rules are hidden by default without hiding the archive selector',()=>{
 const html=renderToStaticMarkup(React.createElement(Rules,{items:[{id:'old',text:'ARCHIVED_TEXT',kind:'REVIEW_RULE',scope:'global',enabled:false}],products:[],owner:true,onRefresh(){},onPropose(){},onDelete(){}}));
 assert(!html.includes('ARCHIVED_TEXT'));
 assert(html.includes('Отключённые'));
 assert(html.includes('Все, включая историю'));
});
