const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
const source=fs.readFileSync(path.join(__dirname,'../app/ReviewCard.tsx'),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS}}).outputText;
const moduleObject={exports:{}};
new Function('require','module','exports',compiled)(require,moduleObject,moduleObject.exports);
const Card=moduleObject.exports.default;
function render(overrides={}){
 const review={id:'r',product_id:'p',product:{name:'Очиститель',brand:'KANGAROO'},rating:5,text:'',risk:'NORMAL',created_at:'2026-10-13',draft:{text:'Спасибо за оценку!',revision:2,quality:{passed:true},edits:[{at:'2026-10-13',old:'Прежний текст',new:'Новый текст'}]},...overrides.review};
 return renderToStaticMarkup(React.createElement(Card,{review,checked:false,toggle(){},busy:false,assistantEnabled:true,canManage:true,generating:false,generationError:'',generate(){},save(){},feedback(){},...overrides,review}));
}
test('rating-only card removes the empty customer block',()=>{const html=render();assert(!html.includes('ОТЗЫВ ПОКУПАТЕЛЯ'));assert(html.includes('Оценка без текста'));assert(html.includes('Готов к отправке'))});
test('low-rated draft needs review despite a passed text score',()=>{const html=render({review:{rating:2,text:'Плохое качество'}});assert(html.includes('Нужно проверить'));assert(!html.includes('Готов к отправке'));assert(html.includes('has-text'))});
test('history is collapsed by default',()=>{const html=render();assert(html.includes('История правок'));assert(html.includes('Прежний текст'));assert(html.includes('Новый текст'));assert(!html.includes(' open='))});
test('generation retains text and disables editor',()=>{const html=render({generating:true,busy:true});assert(html.includes('Генерируется'));assert(html.includes('Спасибо за оценку!'));assert(/textarea[^>]+disabled/.test(html));assert(html.includes('0 сек.'))});
test('recoverable waiting failure retains text and offers continuation',()=>{const html=render({generationError:'Ответ ещё готовится на сервере.'});assert(html.includes('Продолжить ожидание'));assert(html.includes('Спасибо за оценку!'));assert(html.includes('Предыдущий ответ сохранён'))});
test('terminal failure has a retry action',()=>{assert(render({generationError:'Не удалось завершить подготовку.'}).includes('Повторить попытку'))});
test('published review offers no regeneration or save',()=>{const html=render({review:{is_answered:true,existing_answer:'Опубликованный ответ'}});assert(html.includes('Отправлен'));assert(!html.includes('Новый вариант'));assert(!html.includes('Сохранить</button>'))});
exports.Card=Card;
