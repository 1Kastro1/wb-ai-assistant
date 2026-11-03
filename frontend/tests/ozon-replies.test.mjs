import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {dirname} from 'node:path';
const require=createRequire(import.meta.url),__dirname=dirname(fileURLToPath(import.meta.url));
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),ts=require('typescript');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
function load(file){
 const compiled=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const module={exports:{}};
 const localRequire=name=>{
  if(name.endsWith('.css'))return {};
  if(!name.startsWith('.'))return require(name);
  const roots=[path.resolve(path.dirname(file),name),path.resolve(__dirname,'../app',name)];
  const candidates=roots.flatMap(p=>[p,p+'.tsx',p+'.ts',p+'.mjs']);
  return load(candidates.find(p=>fs.existsSync(p)));
 };
 new Function('require','module','exports',compiled)(localRequire,module,module.exports);return module.exports;
}
const Card=load(process.env.OZON_CARD_FILE||path.resolve(__dirname,'../app/ReviewCard.tsx')).default;
function render(extra={}){
 const review={id:'r',marketplace:'ozon',is_answered:true,status:'published',existing_answer:'',rating:5,text:'Отлично',risk:'NORMAL',product:{name:'Товар',brand:'Бренд'},...extra};
 return renderToStaticMarkup(React.createElement(Card,{review,checked:false,toggle(){},busy:false,assistantEnabled:true,canManage:true,generating:false,generationError:'open',generate(){},save(){},feedback(){}}));
}
test('empty remote text never shows an empty blockquote or a sent claim',()=>{
 const html=render();assert(html.includes('Статус ответа уточняется'));assert(html.includes('Текст ответа пока не загружен из Ozon'));
 assert(!html.includes('published-reply'));assert(!html.includes('Отправлено'));
});
test('verified seller reply is visible',()=>{
 const html=render({existing_answer:'Добрый день! Спасибо за отзыв.'});
 assert(html.includes('Отправлено'));assert(html.includes('Добрый день! Спасибо за отзыв.'));assert(html.includes('published-reply'));
});
test('moderating comment is shown without claiming publication',()=>{
 const html=render({status:'answer_pending',existing_answer:'Спасибо за отзыв.'});
 assert(html.includes('На модерации Ozon'));assert(html.includes('Ozon ещё не подтвердил публикацию'));assert(!html.includes('Отправлено'));
});
