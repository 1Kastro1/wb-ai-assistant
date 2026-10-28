const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
function loadComponent(file,source=fs.readFileSync(file,'utf8')){
 const compiled=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const moduleObject={exports:{}};
 const localRequire=name=>{if(name.endsWith('.css'))return {};if(!name.startsWith('.'))return require(name);const target=path.resolve(path.dirname(file),name);return loadComponent([target,target+'.tsx',target+'.ts',target+'.mjs'].find(file=>fs.existsSync(file))||target)};
 new Function('require','module','exports',compiled)(localRequire,moduleObject,moduleObject.exports);
 return moduleObject.exports;
}
test('store-specific product panels have distinct sibling keys on every marketplace switch',()=>{
 const file=path.join(__dirname,'../app/page.tsx'),source=fs.readFileSync(file,'utf8');
 const ast=ts.createSourceFile(file,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),keys={};
 function visit(node){
  if(ts.isJsxSelfClosingElement(node)&&['ProductKnowledgeOverview','CrossCatalog','ProductLibrary'].includes(node.tagName.getText(ast))){
   const key=node.attributes.properties.find(a=>ts.isJsxAttribute(a)&&a.name.text==='key');
   assert(key&&key.initializer&&ts.isJsxExpression(key.initializer));
   keys[node.tagName.getText(ast)]=key.initializer.expression.getText(ast);
  }
  ts.forEachChild(node,visit);
 }
 visit(ast);assert.equal(Object.keys(keys).length,3);
 for(const active of ['wb','ozon','yandex','wb']){
  const evaluated=Object.values(keys).map(expression=>new Function('stores','return '+expression)({active}));
  assert.equal(new Set(evaluated).size,3,'Sibling keys must identify the panel as well as its store');
  assert(evaluated.every(key=>key.includes(active)));
 }
});
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
test('rule verification status distinguishes checked and unverified answers',()=>{
 for(const passed of [true,false]){
  const html=render({generationError:'Откройте ответ',review:{draft:{text:'Добрый день! Спасибо за отзыв.',revision:2,quality:{passed,owner_rules:{matched:3,passed}},edits:[]}}});
  assert(html.includes(passed?'Правила магазина выполнены':'Найдены замечания по правилам'));
  assert(!html.includes(passed?'Правила магазина требуют проверки':'Правила магазина выполнены'));
 }
});
exports.Card=Card;

test('timeout and pending checks are distinct from text violations',()=>{
 const {OwnerRuleStatus}=loadComponent(path.join(__dirname,'../app/ReviewCard.tsx'));
 function status(rules){return renderToStaticMarkup(React.createElement(OwnerRuleStatus,{quality:{owner_rules:{matched:3,passed:false,...rules}}}))}
 const timeout=status({checked:false,technical_error:true,reason:'истекло время проверки',issues:['таймаут'],violations:['Не выполнена подпись ответа']});
 assert(timeout.includes('Сбой проверки правил'));assert(timeout.includes('Замечания к тексту'));assert(timeout.includes('Не выполнена подпись ответа'));
 const pending=status({checked:false,issues:['ожидает проверки']});
 assert(pending.includes('Ожидает проверки правил'));assert(!pending.includes('Найдены замечания по правилам'));
 assert(status({checked:true,issues:['не учтена упаковка']}).includes('не учтена упаковка'));
});

const UniqomFitment=loadComponent(path.join(__dirname,'../app/UniqomFitment.tsx')).default;
const uniqomLinks=loadComponent(path.join(__dirname,'../app/uniqomCatalog.ts'));
test('Uniqom opens one exact Frame and never embeds an ambiguous vehicle choice',()=>{
 assert.equal(uniqomLinks.uniqomVehicleUrl('Подойдёт ZRT260-0000001?'),'https://uniqom.ru/original-catalogs?identString=ZRT260-0000001');
 assert.equal(uniqomLinks.uniqomVehicleUrl('ZRT260-0000001 или GB3-0000001'),'https://uniqom.ru/original-catalogs');
 assert(uniqomLinks.uniqomOemUrl('04465-02220').includes('term=04465-02220'));
 assert.equal(uniqomLinks.uniqomOemUrl('javascript:alert(1)'),'https://uniqom.ru/original-catalogs');
});
test('all-category Uniqom workflow remains visible without automatic provider access',()=>{
 const html=renderToStaticMarkup(React.createElement(UniqomFitment,{question:{id:'q',text:'ZRT260-0000001'},product:{brand:'ADVICS',part_number:'SN138',category:'Колодки'},coverage:{automatic_supported:false,group_id:'brakes',systems:[{id:'brakes',label:'Тормозная система'},{id:'other',label:'Другая категория'}]}}));
 assert(html.includes('Все категории'));
 assert(html.includes('Открыть автомобиль в Uniqom'));
 assert(html.includes('Тормозная система'));
 assert(html.includes('SN138'));
 assert(html.includes('переднюю или заднюю ось'));
 assert(html.includes('Сохраните проверенный подбор'));
 assert(!html.includes('disabled'));
 assert(!html.includes('совместимость подтверждена'));
});


test('product photos load lazily with an accessible name',()=>{const html=render({review:{product:{name:'Очиститель',brand:'KANGAROO',image_url:'https://example.com/card.webp'}}});assert(html.includes('src="https://example.com/card.webp"'));assert(html.includes('loading="lazy"'));assert(html.includes('alt="Очиститель"'))});
test('missing or unsafe photo URLs retain a product placeholder',()=>{for(const image_url of ['', 'javascript:alert(1)']){const html=render({review:{product:{name:'Очиститель',image_url}}});assert(html.includes('Фото товара отсутствует'));assert(!html.includes('<img'))}});
test('review header shows status and collapsed scheduling without action buttons',()=>{const Header=loadComponent(path.join(__dirname,'../app/ReviewHeader.tsx')).default;const html=renderToStaticMarkup(React.createElement(Header,{automation:{enabled:false},count:30,total:220294}));assert(html.includes('Расписание автоответов'));assert(!html.includes(' open='));assert(html.includes('Автоответы:'));assert(!html.includes('<button'));assert(!html.includes('Проверить сейчас'));assert(!html.includes('К публикации'));});

test('product knowledge card renders its own thumbnail',()=>{
 const file=path.join(__dirname,'../app/page.tsx'),page=fs.readFileSync(file,'utf8');
 const start=page.indexOf('function ProductKnowledgeCard('),end=page.indexOf('\nfunction reviewIntent',start);
 const fragment="import {useState,useEffect} from 'react'; import ActionButton from './ActionButton'; import ProductThumbnail from './ProductThumbnail'; import {ProductFacts,productArticle,trustedProductFact} from './ProductLibrary'; type Row=Record<string,any>;"+page.slice(start,end)+';export default ProductKnowledgeCard;';
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


test('density controls expose the selected mode without submitting forms',()=>{
 const Control=loadComponent(path.join(__dirname,'../app/DensityControl.tsx')).default;
 const html=renderToStaticMarkup(React.createElement(Control,{value:'compact',onChange(){}}));
 assert(html.includes('Плотность списков'));assert(html.includes('Просторный'));assert(html.includes('Компактный'));
 assert.equal((html.match(/aria-pressed="true"/g)||[]).length,1);
 assert.equal((html.match(/type="button"/g)||[]).length,2);
});
test('product drawer has a native modal, accessible heading and explicit close action',()=>{
 const Drawer=loadComponent(path.join(__dirname,'../app/ProductDrawer.tsx')).default;
 const html=renderToStaticMarkup(React.createElement(Drawer,{name:'Фильтр',busy:true,error:'Ошибка сервера',onClose(){}},'Характеристики'));
 assert(html.includes('<dialog'));assert(html.includes('aria-labelledby='));assert(html.includes('Закрыть карточку'));
 assert(html.includes('disabled'));assert(html.includes('role="alert"'));assert(html.includes('Ошибка сервера'));
});
test('action buttons preserve an accessible name and permission-based disabling',()=>{
 const Button=loadComponent(path.join(__dirname,'../app/ActionButton.tsx')).default;
 const html=renderToStaticMarkup(React.createElement(Button,{label:'Найти',disabled:true,onAction:async()=>true}));
 assert(html.includes('aria-label="Найти"'));assert(html.includes('aria-busy="false"'));assert(html.includes('disabled'));
 assert(html.includes('type="button"'));
});

const Connection=loadComponent(path.join(__dirname,'../app/MarketplaceConnection.tsx')).default;
function syncCard(overrides={}){const success={tone:'success',title:'20.10.2026, 14:00',count:9717,detail:'Обработано: 9717'};return renderToStaticMarkup(React.createElement(Connection,{store:{id:'owner',provider:'wb',config:{}},stores:{active:'owner',items:[{id:'owner',name:'Магазин',provider:'wb'}]},name:'Wildberries',connected:true,owner:true,canManage:true,busy:false,token:'',config:{},status:{state:'idle'},onSelect(){},onSave(){},onTest(){},onSync(){},onHistory(){},productSync:success,reviewSync:success,questionSync:success,...overrides}));}
test('sync cards keep a queued job distinct from its previous successful import',()=>{const html=syncCard({questionSync:{tone:'working',state:'queued',progress:0,title:'В очереди',detail:'Прогресс 0%'}});assert(html.includes('Ожидает запуска'));assert(html.includes('Начнётся после текущего задания'));assert(!html.includes('aria-valuenow="0"'));assert(html.includes('aria-label="Синхронизировать вопросы"'));assert(html.includes('9'));});
test('sync progress has a bounded accessible value and preserves independent controls',()=>{const html=syncCard({status:{state:'working',kind:'reviews',phase:'running',progress:37,message:'Загрузка'}});assert(html.includes('aria-valuenow="37"'));assert(html.includes('37%'));assert(html.includes('Обновить товары'));assert(html.includes('Обновить вопросы'));assert(!html.includes('sync-overview'));});
test('sync operations remain disabled for a viewer',()=>{const html=syncCard({canManage:false});for(const label of ['товары','отзывы','вопросы'])assert(new RegExp('button[^>]+disabled[^>]+aria-label="Синхронизировать '+label+'"').test(html));});
test('question recovery continues the saved job instead of suggesting a new generation',()=>{const html=renderQuestion({generationError:'Ответ ещё готовится на сервере.'});assert(html.includes('Продолжить ожидание'));});

test('catalog progress displays committed count instead of an invented percentage',()=>{const html=syncCard({productSync:{tone:'working',state:'running',progress:95,processed:14300,phase:'archived'}});assert(html.includes('14'));assert(html.includes('300'));assert(html.includes('Загружаем архивные товары'));assert(!html.includes('95%'));assert(!html.includes('aria-valuenow="95"'));});


test('terminal batch stays compact but exposes saved answers and issues without claiming continued processing',()=>{
 const Panel=loadComponent(path.join(__dirname,'../app/BatchDraftPanel.tsx')).default;
 const html=renderToStaticMarkup(React.createElement(Panel,{job:{id:'done',status:'completed',result:{total:10,processed:9,failed:1,attention:2}},onCancel:()=>{},onReview:()=>{}}));
 assert(html.includes('is-terminal'));assert(html.includes('Обработка завершена'));assert(html.includes('Сохранено ответов: 9'));
 assert(html.includes('На проверку: 2'));assert(html.includes('Ошибок: 1'));assert(html.includes('Просмотреть ответы'));
 assert(!html.includes(' open='));assert(!html.includes('<progress'));assert(!html.includes('обработка продолжится'));
 const cancelled=renderToStaticMarkup(React.createElement(Panel,{job:{id:'stop',status:'cancelled',result:{processed:4,total:10}},onCancel:()=>{},onReview:()=>{}}));
 assert(cancelled.includes('Подготовка остановлена'));assert(!cancelled.includes('обработка продолжится'));
});
test('full running batch keeps cancellation and progress until server completion',()=>{
 const Panel=loadComponent(path.join(__dirname,'../app/BatchDraftPanel.tsx')).default;
 const html=renderToStaticMarkup(React.createElement(Panel,{job:{id:'running',status:'running',result:{total:44,processed:44}},onCancel:()=>{},onReview:()=>{}}));
 assert(html.includes('Завершаем подготовку'));assert(html.includes('Остановить подготовку'));assert(html.includes('<progress'));assert(!html.includes('Обработка завершена'));
});
