'use client';
import {useRef,useState} from 'react';
import DraftEditor from './DraftEditor';
import ProductThumbnail from './ProductThumbnail';
import InternetFitment from './InternetFitment';
import {GenerationProgress} from './ReviewCard';
import {vehicleIdentifiers} from './vehicleIdentifiers';
type Row=Record<string,any>;
export default function QuestionCard({question:q,checked,toggle,busy,assistantEnabled,canManage,generating,generationError,generate,save,publish,onRegister,storageKey,generationPhase='running'}:{question:Row;checked:boolean;toggle:()=>void;busy:boolean;assistantEnabled:boolean;canManage:boolean;generating:boolean;generationError:string;generate:(instruction?:string)=>void;save:(text:string,revision:number)=>Promise<Row>;publish:()=>Promise<void>;onRegister?:(id:string,flush:(()=>Promise<any>)|null)=>void|(()=>void);storageKey:string;generationPhase?:string}){
 const [open,setOpen]=useState(false),[opened,setOpened]=useState(false),[dirty,setDirty]=useState(false),[copy,setCopy]=useState(''),[localError,setLocalError]=useState('');
 const flusher=useRef<(()=>Promise<any>)|null>(null),quality=q.draft?.quality||{},fit=quality.fitment||{};
 const ids=vehicleIdentifiers(q.text||''),confirmed=['FIT','ALTERNATIVE'].includes(fit.status),needsCheck=!quality.passed||['NEEDS_CATALOG','CONFLICT'].includes(fit.status);
 function register(id:string,flush:(()=>Promise<any>)|null){flusher.current=flush;return onRegister?.(id,flush)}
 async function copyId(value:string){try{await navigator.clipboard.writeText(value);setCopy(value)}catch{setLocalError('Не удалось скопировать номер. Выделите его и скопируйте вручную.')}}
 async function preparePublication(){setLocalError('');try{await flusher.current?.();await publish()}catch(e){setLocalError(e instanceof Error?e.message:'Не удалось подготовить публикацию')}}
 return <article className="card review compact-review question-card">
  <div className="review-head"><label className="check"><input type="checkbox" aria-label={'Выбрать вопрос о товаре '+q.product.name} checked={checked} disabled={q.is_answered||!canManage} onChange={toggle}/><span className="review-product"><ProductThumbnail product={q.product}/><span className="review-product-copy"><b>{q.product.name}</b><span className="review-meta">{q.product.brand} · {q.marketplace?.startsWith('ozon')?'Ozon':q.marketplace?.startsWith('yandex')?'Яндекс Маркет':'WB'} {q.product_id} · {new Date(q.created_at).toLocaleDateString('ru-RU')}<span className="pill">{q.question_type||'Вопрос'}</span></span></span></span></label><span className={'pill '+(generating?'working':q.is_answered?'green':!q.draft||needsCheck?'amber':'green')}>{q.is_answered?'Отправлено':generating?'Готовится':!q.draft?'Нужен ответ':needsCheck?'Нужна проверка':'Готов к отправке'}</span></div>
  <details className="review-expand" open={open||generating} onToggle={e=>{if(e.currentTarget.open)setOpened(true);if(!generating)setOpen(e.currentTarget.open)}}><summary><span className="review-preview">{q.text}</span><span className="review-open-label">{open||generating?'Свернуть':'Открыть ответ'}</span></summary>
   {(opened||open||generating||generationError)&&<>
    <div className="review-content has-text"><div className="customer-review"><small>ВОПРОС ПОКУПАТЕЛЯ</small><p className="review-text">{q.text}</p>{ids.length>0&&<div className="identifier-chips">{ids.map(id=><button key={id} onClick={()=>void copyId(id)} title="Скопировать номер автомобиля"><span>{id.length===17?'VIN':'Frame'}</span><code>{id}</code><small>{copy===id?'Скопировано':'Копировать'}</small></button>)}</div>}</div>
    {q.is_answered?<blockquote className="published-reply">{q.existing_answer}</blockquote>:<div className="draft">
     {q.draft?<DraftEditor draft={q.draft} disabled={generating||!canManage} storageKey={storageKey} onSave={save} onDirty={setDirty} onRegister={register}/>:<p className="muted">Подготовьте короткий ответ на вопрос покупателя.</p>}
     {generationError&&<div className="generation-notice" role="status">{generationError}{q.draft&&<small>Предыдущий текст сохранён.</small>}</div>}{localError&&<div className="save-error" role="alert">{localError}</div>}
     <div className="draft-actions primary-actions"><button className={q.draft?'new-variant':'primary'} disabled={busy||generating||dirty||!canManage||!assistantEnabled} onClick={()=>generate()}>{generating?'Готовим…':generationError?(/готовится|ожидание|связь|Связь/.test(generationError)?'Продолжить ожидание':'Повторить попытку'):q.draft?'Новый вариант':'Подготовить ответ'}</button>{q.draft&&<button className="primary" disabled={busy||generating||!canManage||['NEEDS_CATALOG','CONFLICT'].includes(fit.status)} onClick={()=>void preparePublication()}>Подготовить публикацию</button>}</div>
     {generating&&<GenerationProgress phase={generationPhase}/>}
     {q.draft&&<details className="reply-details"><summary>Проверка и источники ответа</summary>{quality.issues?.length>0&&<p className="orange">{quality.issues.join('; ')}</p>}<p className="muted">Оценка текста: {quality.score??'—'}/100 · {q.draft.model}</p>{quality.analysis_summary&&<p>{quality.analysis_summary}</p>}{quality.sources?.map((source:Row,i:number)=><div className="source-row" key={i}><b>{source.verification_status==='VERIFIED'?'Подтверждено':'Нужна проверка'}</b><p>{source.text||source.snippet}</p>{/^https:\/\//.test(source.source||source.url||'')?<a href={source.source||source.url} target="_blank" rel="noopener noreferrer">Открыть источник ↗</a>:<small>{source.source||source.url}</small>}</div>)}</details>}
    </div>}</div>
    {fit.status&&<div className={'fitment-result '+(confirmed?'confirmed':'unconfirmed')}><div className="fitment-steps"><span className={ids.length?'done':''}>1 · Автомобиль</span><span className={(confirmed||fit.oe_number||fit.internet?.oem_candidates?.length)?'done':''}>2 · Проверка каталога</span><span className={confirmed?'done':''}>3 · Наш товар</span></div><b>{confirmed?'Совместимость подтверждена':fit.status==='UNAVAILABLE'?'Подходящего товара нет в подтверждённой выборке':'Подбор требует уточнения'}</b><p>{fit.pending_reason||fit.text||'Основания подбора доступны в источниках ответа.'}</p></div>}
    {!q.is_answered&&(fit.status||ids.length>0)&&<InternetFitment question={q} initial={fit.internet} canManage={canManage} onSaved={()=>generate()}/>}
   </>}
  </details>
 </article>;
}
