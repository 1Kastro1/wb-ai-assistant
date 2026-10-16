const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
function loadComponent(file,source=fs.readFileSync(file,'utf8')){
 const compiled=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS}}).outputText;
 const moduleObject={exports:{}};
 const localRequire=name=>{if(!name.startsWith('.'))return require(name);const target=path.resolve(path.dirname(file),name);return loadComponent([target,target+'.tsx',target+'.ts',target+'.mjs'].find(file=>fs.existsSync(file))||target)};
 new Function('require','module','exports',compiled)(localRequire,moduleObject,moduleObject.exports);
 return moduleObject.exports;
}
const Card=loadComponent(path.join(__dirname,'../app/ReviewCard.tsx')).default;
function render(overrides={}){
 const review={id:'r',product_id:'p',product:{name:'Очиститель',brand:'KANGAROO'},rating:5,text:'',risk:'NORMAL',created_at:'2026-10-13',draft:{text:'Спасибо за оценку!',revision:2,quality:{passed:true},edits:[{at:'2026-10-13',old:'Прежний текст',new:'Новый текст'}]},...overrides.review};
 return renderToStaticMarkup(React.createElement(Card,{review,checked:false,toggle(){},busy:false,assistantEnabled:true,canManage:true,generating:false,generationError:'',generate(){},save(){},feedback(){},...overrides,review}));
}
test('rating-only card removes the empty customer block',()=>{const html=render();assert(!html.includes('ОТЗЫВ ПОКУПАТЕЛЯ'));assert(html.includes('Оценка без текста'));assert(html.includes('Готов к отправке'))});
test('low-rated draft needs review despite a passed text score',()=>{const html=render({review:{rating:2,text:'Плохое качество'}});assert(html.includes('Нужна проверка'));assert(!html.includes('Готов к отправке'));assert(!html.includes('textarea'))});
test('collapsed rows do not mount editors or edit history',()=>{const html=render();assert(!html.includes('textarea'));assert(!html.includes('История правок'));assert(!html.includes('Прежний текст'));assert(!html.includes(' open='));assert(html.includes('Открыть ответ'))});
test('generation retains text and disables editor',()=>{const html=render({generating:true,busy:true});assert(html.includes('Готовится'));assert(html.includes('Спасибо за оценку!'));assert(/textarea[^>]+disabled/.test(html));assert(html.includes('0 сек.'))});
test('recoverable waiting failure retains text and offers continuation',()=>{const html=render({generationError:'Ответ ещё готовится на сервере.'});assert(html.includes('Продолжить ожидание'));assert(html.includes('Спасибо за оценку!'));assert(html.includes('Предыдущий ответ сохранён'))});
test('terminal failure has a retry action',()=>{assert(render({generationError:'Не удалось завершить подготовку.'}).includes('Повторить попытку'))});
test('published review offers no regeneration or save',()=>{const html=render({review:{is_answered:true,existing_answer:'Опубликованный ответ'}});assert(html.includes('Отправлено'));assert(!html.includes('Новый вариант'));assert(!html.includes('Сохранить</button>'))});
exports.Card=Card;

test('product photos load lazily with an accessible name',()=>{const html=render({review:{product:{name:'Очиститель',brand:'KANGAROO',image_url:'https://example.com/card.webp'}}});assert(html.includes('src="https://example.com/card.webp"'));assert(html.includes('loading="lazy"'));assert(html.includes('alt="Очиститель"'))});
test('missing or unsafe photo URLs retain a product placeholder',()=>{for(const image_url of ['', 'javascript:alert(1)']){const html=render({review:{product:{name:'Очиститель',image_url}}});assert(html.includes('Фото товара отсутствует'));assert(!html.includes('<img'))}});
test('review header keeps scheduling details collapsed and controls permission-aware',()=>{const Header=loadComponent(path.join(__dirname,'../app/ReviewHeader.tsx')).default;const html=renderToStaticMarkup(React.createElement(Header,{automation:{enabled:false},busy:false,canManage:false,assistantEnabled:true,count:30,total:220294,selected:0,onToggle(){},onCheck(){},onPublish(){}}));assert(html.includes('Расписание автоответов'));assert(!html.includes(' open='));assert(html.includes('disabled'));assert(!html.includes('class="primary"'))});

test('product knowledge card renders its own thumbnail',()=>{
 const file=path.join(__dirname,'../app/page.tsx'),page=fs.readFileSync(file,'utf8');
 const start=page.indexOf('function ProductKnowledgeCard('),end=page.indexOf('\nfunction reviewIntent',start);
 const fragment="import {useState,useEffect} from 'react'; import ProductThumbnail from './ProductThumbnail'; import {ProductFacts,productArticle,trustedProductFact} from './ProductLibrary'; type Row=Record<string,any>;"+page.slice(start,end)+';export default ProductKnowledgeCard;';
 const KnowledgeCard=loadComponent(file,fragment).default;
 const html=renderToStaticMarkup(React.createElement(KnowledgeCard,{product:{name:'Очиститель',brand:'KANGAROO',image_url:'https://example.com/card.webp',facts:[]},busy:false,owner:false,canManage:true,onDiscuss(){},onRefresh(){},onRun(){}}));
 assert(html.includes('src="https://example.com/card.webp"'));
 assert(html.includes('product-identity'));
 const otherStart=page.indexOf('function SyncStatus'),otherEnd=page.indexOf('function ActionCard',otherStart);
 assert(otherStart>=0&&otherEnd>otherStart);
 assert(!page.slice(otherStart,page.indexOf('function ProductKnowledgeCard',otherStart)).includes('ProductThumbnail'));
});

const Question=loadComponent(path.join(__dirname,'../app/QuestionCard.tsx')).default;
function renderQuestion(overrides={}){return renderToStaticMarkup(React.createElement(Question,{question:{id:'q',product_id:'42',product:{name:'Фильтр',brand:'NITTO'},text:'Подойдет? TSMJYBA2S00828853',created_at:'2026-10-16',draft:{id:'d',text:'Добрый день! Вам подойдет данный товар.',revision:1,quality:{passed:true,fitment:{status:'FIT'}}}},checked:false,toggle(){},busy:false,assistantEnabled:true,canManage:true,generating:false,generationError:'',generate(){},save:async()=>({}),publish:async()=>{},storageKey:'question-test',...overrides}))}
test('question cards defer editors until opened and keep vehicle data out of preview answers',()=>{const html=renderQuestion();assert(!html.includes('textarea'));assert(html.includes('Открыть ответ'));assert(html.includes('Готов к отправке'))});
test('question generation retains saved text with a disabled editor',()=>{const html=renderQuestion({generating:true});assert(html.includes('Вам подойдет данный товар'));assert(/textarea[^>]+disabled/.test(html));assert(html.includes('VIN'));assert(html.includes('Подготовить публикацию'))});
test('question publication is permission-aware',()=>{const html=renderQuestion({generating:true,canManage:false});assert(/button[^>]+disabled[^>]*>Подготовить публикацию/.test(html))});
test('instruction indicator rejects untrusted verified URLs',()=>{const {instructionState}=loadComponent(path.join(__dirname,'../app/ProductLibrary.tsx'));assert.equal(instructionState({facts:[{kind:'instruction',verification_status:'VERIFIED',source:'https://untrusted.test'}]}).tone,'neutral');assert.equal(instructionState({facts:[{kind:'instruction',verification_status:'VERIFIED',source:'Карточка товара'}]}).tone,'green')});

test('manufacturer article comes from verified WB characteristics, never seller SKU',()=>{const {productArticle}=loadComponent(path.join(__dirname,'../app/ProductLibrary.tsx'));assert.equal(productArticle({vendor_code:'seller',facts:[{source:'Карточка WB: характеристики',verification_status:'VERIFIED',text:'Артикул производителя: DFF12'}]}),'DFF12');assert.equal(productArticle({vendor_code:'seller',facts:[{source:'other',verification_status:'VERIFIED',text:'Артикул производителя: WRONG'}]}),'')});
