'use client';
import {useState} from 'react';
import {api} from './client';

type Row=Record<string,any>;
type RuleKind='REVIEW_RULE'|'QUESTION_RULE';
const scopes:Record<string,string>={global:'Все товары',brand:'Бренд',product:'Товар',category:'Категория'};
const kindLabels:Record<string,string>={REVIEW_RULE:'Для отзывов',QUESTION_RULE:'Для вопросов',GLOBAL_RULE:'Отзывы и вопросы (старое правило)'};
const matchesKind=(item:Row,kind:RuleKind)=>!['REVIEW_RULE','QUESTION_RULE'].includes(item.kind)||item.kind===kind;

export default function MemoryRules({items,products,owner,onRefresh,onPropose,onDelete,initialKind='REVIEW_RULE'}:{items:Row[];products:Row[];owner:boolean;onRefresh:()=>Promise<void>;onPropose:(text:string,scope:string,kind:RuleKind)=>Promise<void>;onDelete:(id:string)=>void;initialKind?:RuleKind}){
 const [audience,setAudience]=useState<RuleKind>(initialKind);
 const [filter,setFilter]=useState('all'),[search,setSearch]=useState(''),[rule,setRule]=useState(''),[scopeKind,setScopeKind]=useState('global'),[target,setTarget]=useState('');
 const [example,setExample]=useState(''),[rating,setRating]=useState(3),[product,setProduct]=useState(''),[result,setResult]=useState<Row|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const questions=audience==='QUESTION_RULE';
 const sectionItems=items.filter(m=>matchesKind(m,audience));
 const visible=sectionItems.filter(m=>(filter==='all'||(m.scope||'global').split(':')[0]===filter)&&m.text.toLocaleLowerCase('ru').includes(search.toLocaleLowerCase('ru')));
 async function run(fn:()=>Promise<void>){setBusy(true);setError('');try{await fn()}catch(e){setError(e instanceof Error?e.message:'Не удалось выполнить действие')}finally{setBusy(false)}}
 function changeAudience(value:RuleKind){setAudience(value);setRule('');setScopeKind('global');setTarget('');setExample('');setProduct('');setResult(null);setError('')}
 return <section>
  <div className="section-title"><div><p className="eyebrow">ПРАВИЛА ВАШЕГО МАГАЗИНА</p><h1>Правила ответов</h1><p className="muted">Создавайте правила отдельно для отзывов и вопросов. Каждый раздел действует на Wildberries, Ozon и Яндекс Маркете. Область товара, бренда или категории выбирается независимо от типа ответа.</p></div><span className="pill">{sectionItems.filter(m=>m.enabled).length} действуют</span></div>
  <div className="segmented rule-kind-tabs" role="tablist" aria-label="Тип правил">
   {(['REVIEW_RULE','QUESTION_RULE'] as RuleKind[]).map(value=><button key={value} id={'rules-'+value} role="tab" aria-selected={audience===value} aria-pressed={audience===value} aria-controls="rule-kind-panel" disabled={busy} onClick={()=>changeAudience(value)}>{kindLabels[value]} <span className="pill">{items.filter(m=>matchesKind(m,value)).length}</span></button>)}
  </div>
  {error&&<div className="error" role="alert">{error}</div>}
  <div id="rule-kind-panel" role="tabpanel" aria-labelledby={'rules-'+audience} className="rules-layout" style={{marginTop:20}}>
   <div>
    <div className="toolbar"><input aria-label="Поиск правила" type="search" placeholder="Слово или текст правила" value={search} onChange={e=>setSearch(e.target.value)}/></div>
    <div className="segmented" role="group" aria-label="Область правил">{[['all','Все'],['global','Общие'],['brand','Бренды'],['product','Товары'],['category','Категории']].map(([id,label])=><button key={id} aria-pressed={filter===id} onClick={()=>setFilter(id)}>{label}</button>)}</div>
    <div style={{marginTop:16}}>{visible.map(m=><article key={m.id} className={'card rule-card '+(!m.enabled?'disabled':'')}>
     <div className="card-heading"><span className="pill">{scopes[(m.scope||'global').split(':')[0]]||m.scope}{m.scope?.includes(':')?' · '+m.scope.split(':').slice(1).join(':'):''}</span><label className="rule-switch"><input type="checkbox" role="switch" aria-label={'Включить правило '+m.text.slice(0,60)} checked={Boolean(m.enabled)} disabled={busy||!owner} onChange={e=>{const enabled=e.target.checked;void run(async()=>{await api('/memory/'+encodeURIComponent(m.id)+'/enabled','PATCH',{enabled});await onRefresh()})}}/>{m.enabled?'Включено':'Отключено'}</label></div>
     <p>{m.text}</p><label>Применять правило<select aria-label={'Тип правила: '+m.text.slice(0,60)} value={['REVIEW_RULE','QUESTION_RULE'].includes(m.kind)?m.kind:'GLOBAL_RULE'} disabled={busy||!owner} onChange={e=>{const kind=e.target.value;void run(async()=>{await api('/memory/'+encodeURIComponent(m.id)+'/kind','PATCH',{kind});setResult(null);await onRefresh()})}}>{!['REVIEW_RULE','QUESTION_RULE'].includes(m.kind)&&<option value="GLOBAL_RULE" disabled>{kindLabels.GLOBAL_RULE}</option>}<option value="REVIEW_RULE">Только для отзывов</option><option value="QUESTION_RULE">Только для вопросов</option></select></label>
     <p className="rule-meta">{m.created_by_name?'Добавил: '+m.created_by_name:'Автор старого правила не записан'}{m.created_at?' · '+new Date(m.created_at).toLocaleDateString('ru-RU'):''}{m.updated_by_name?' · Изменил: '+m.updated_by_name:''}</p>
     <div className="button-row"><button disabled={busy} onClick={()=>{setRule(m.text);setScopeKind((m.scope||'global').split(':')[0]);setTarget((m.scope||'').split(':').slice(1).join(':'));setResult(null);document.getElementById('rule-example')?.focus()}}>Проверить на примере</button>{owner&&<button disabled={busy} onClick={()=>onDelete(m.id)}>Удалить</button>}</div>
    </article>)}</div>
    {!visible.length&&<div className="empty"><h3>{sectionItems.length?'Совпадений нет':questions?'Правил для вопросов пока нет':'Правил для отзывов пока нет'}</h3><p>Измените поиск или предложите новое правило в этом разделе.</p></div>}
   </div>
   <aside className="card rule-preview">
    <h3>Новое правило {questions?'для вопросов':'для отзывов'}</h3><p className="muted">Будет применяться только к {questions?'вопросам':'отзывам'} на всех маркетплейсах.</p>
    <label>Что учитывать в ответах<textarea disabled={busy} value={rule} maxLength={2000} onChange={e=>{setRule(e.target.value);setResult(null)}} placeholder={questions?'Например: проверяй нужную деталь по тексту вопроса.':'Например: добавляй подпись в конце ответа на отзыв.'}/></label>
    <div className="rule-scope"><label>Область<select disabled={busy} value={scopeKind} onChange={e=>{setScopeKind(e.target.value);setTarget('');setResult(null)}}>{Object.entries(scopes).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>{scopeKind!=='global'&&<label>{scopes[scopeKind]}<input disabled={busy} value={target} onChange={e=>setTarget(e.target.value)} placeholder={scopeKind==='product'?'Артикул товара':'Название'}/></label>}</div>
    <button className="primary full" disabled={busy||rule.trim().length<2||scopeKind!=='global'&&!target.trim()} onClick={()=>void run(async()=>{await onPropose(rule.trim(),scopeKind==='global'?'global':scopeKind+':'+target.trim(),audience);setRule('');setResult(null)})}>Предложить сохранение</button>
    <hr/><h3>Проверить на примере {questions?'вопроса':'отзыва'}</h3>
    <label>Товар<select disabled={busy} value={product} onChange={e=>{setProduct(e.target.value);setResult(null)}}><option value="">Без привязки к товару</option>{products.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    <label>{questions?'Вопрос покупателя':'Отзыв'}<textarea disabled={busy} id="rule-example" value={example} maxLength={5000} onChange={e=>{setExample(e.target.value);setResult(null)}} placeholder={questions?'Вставьте пример вопроса покупателя':'Вставьте пример отзыва покупателя'}/></label>
    {!questions&&<label>Оценка<select disabled={busy} value={rating} onChange={e=>{setRating(Number(e.target.value));setResult(null)}}>{[1,2,3,4,5].map(n=><option key={n} value={n}>{n} ★</option>)}</select></label>}
    <button disabled={busy||rule.trim().length<2||!example.trim()} onClick={()=>void run(async()=>setResult(await api('/memory/preview','POST',{text:rule,example,...(!questions?{rating}:{}),product_id:product,kind:audience},20000)))}>{busy?'Проверяем…':'Посмотреть пример ответа'}</button>
    {result&&<div className="fitment-result"><b>Предварительный ответ {questions?'на вопрос':'на отзыв'}</b><p style={{whiteSpace:'pre-wrap'}}>{result.text}</p><small>{result.note}</small>{result.quality?.owner_rules&&<p role="status" className={result.quality.owner_rules.passed?'muted':'orange'}>{result.quality.owner_rules.passed?'Правила магазина выполнены':'Правила магазина требуют проверки'}</p>}{result.quality?.issues?.length>0&&<p className="orange">{result.quality.issues.join('; ')}</p>}</div>}
    <p className="muted">Проверка не сохраняет правило и не отправляет ответ покупателю.{questions?' Для VIN / Frame новый поиск в Uniqom в этом примере не запускается.':''}</p>
   </aside>
  </div>
 </section>;
}
