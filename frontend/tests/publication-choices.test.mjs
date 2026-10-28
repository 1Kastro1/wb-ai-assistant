import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const require=createRequire(import.meta.url),__dirname=fileURLToPath(new URL('.',import.meta.url));
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const ts=require('typescript');
const React=require('react');
const {renderToStaticMarkup}=require('react-dom/server');
function component(name){
 const source=fs.readFileSync(path.join(__dirname,'../app',name),'utf8');
 const compiled=ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const moduleObject={exports:{}};
 const localRequire=id=>id==='./client'?{api:()=>{throw Error('Rendering must not start publication')}}:id==='./ApprovalDialog'?{default:()=>null}:id==='./PublicationChoices'?{default:component('PublicationChoices.tsx')}:id==='./publication-scope.mjs'?{publicationDestination:()=>({name:'Ozon',short:'Ozon',logo:'O'})}:id.endsWith('.css')?{}:require(id);
 new Function('require','module','exports',compiled)(localRequire,moduleObject,moduleObject.exports);
 return moduleObject.exports.default;
}
test('review toolbar exposes exactly one export button and does not initiate a request',()=>{
 const html=renderToStaticMarkup(React.createElement(component('BulkWbPublish.tsx'),{storeId:'store',provider:'ozon',canManage:true,busy:false,onChange:async()=>{}}));
 assert.equal((html.match(/<button/g)||[]).length,1);
 assert.match(html,/Выгрузить положительные отзывы/);
});
test('two direct export choices have distinct scopes and active launches cannot be repeated',()=>{
 const Choices=component('PublicationChoices.tsx'),calls=[];
 const tree=Choices({loading:false,working:null,disabled:false,statuses:{},onLaunch:scope=>calls.push(scope)});
 const buttons=tree.props.children;
 assert.equal(buttons.length,2);
 buttons[0].props.onClick();buttons[1].props.onClick();assert.deepEqual(calls,['all','positive']);
 const html=renderToStaticMarkup(React.createElement(Choices,{loading:false,working:null,disabled:false,statuses:{positive:{status:'preparing',auto_send:true}},onLaunch:()=>{}}));
 assert.equal((html.match(/<button/g)||[]).length,2);
 assert.match(html,/Выгрузить все отзывы/);assert.match(html,/Выгрузить положительные отзывы/);
 assert.match(html,/disabled/);assert.doesNotMatch(html,/type="radio"/);
});
test('waiting and published answer tabs expose their current selection',()=>{
 const Tabs=component('ReviewAnswerTabs.tsx'),calls=[];
 const tree=Tabs({value:'unanswered',busy:false,onChange:value=>calls.push(value)});
 assert.equal(tree.props.children.length,3);
 assert.equal(tree.props.children[1].props['aria-pressed'],true);
 tree.props.children[2].props.onClick();tree.props.children[0].props.onClick();assert.deepEqual(calls,['answered','all']);
 const html=renderToStaticMarkup(React.createElement(Tabs,{value:'answered',busy:false,onChange:()=>{}}));
 assert.match(html,/Ждут ответа/);assert.match(html,/Есть ответ/);
 assert.equal((html.match(/aria-pressed="true"/g)||[]).length,1);
});

