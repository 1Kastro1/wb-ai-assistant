'use client';
import {useEffect,useState,useRef} from 'react';
import ProductThumbnail from './ProductThumbnail';
import DraftEditor from './DraftEditor';
type Row=Record<string,any>;

export function OwnerRuleStatus({quality}:{quality:Row}){
 const rules=quality.owner_rules;
 if(!rules?.matched)return null;
 return <div className={rules.passed?'muted':'orange'} role="status"><p>{rules.passed?'Правила магазина выполнены':rules.checked===false?'Проверка правил не завершена':'Найдены замечания по правилам'} · Правил: {rules.matched}</p>{!rules.passed&&<ul>{(rules.issues?.length?rules.issues:['Выполнение правил не подтверждено. Подготовьте ответ заново.']).map((issue:string,i:number)=><li key={i}>{issue}</li>)}</ul>}</div>;
}

export function GenerationProgress({phase='running'}:{phase?:string}={}){
  const [seconds,setSeconds]=useState(0);
  useEffect(()=>{const start=Date.now();const timer=setInterval(()=>setSeconds(Math.floor((Date.now()-start)/1000)),1000);return()=>clearInterval(timer)},[]);
  const queued=phase==='queued';
  return <div className="generation-progress" role="status" aria-live="polite"><div className="generation-spinner" aria-hidden="true">✦</div><div className="generation-copy"><b>{queued?'Ожидаем очередь':'Готовим и проверяем ответ'}<span aria-hidden="true"> · {seconds} сек.</span></b><p>{seconds<45?'Можно дождаться результата здесь.':'Подготовка и проверка правил продолжаются на сервере. Можно перейти в другой раздел — задание и предыдущий текст сохраняются.'}</p><ol className="generation-steps" aria-label="Этапы подготовки ответа"><li data-state={queued?'active':'done'} aria-current={queued?'step':undefined}>Очередь<span className="sr-only">{queued?' — текущий этап':' — пройдена'}</span></li><li data-state={queued?'pending':'active'} aria-current={!queued?'step':undefined}>Подготовка и проверка<span className="sr-only">{queued?' — ожидает':' — текущий этап'}</span></li><li data-state="pending">Готовый черновик<span className="sr-only"> — ожидает завершения</span></li></ol></div></div>;
}

export default function ReviewCard({review:r,checked,toggle,busy,assistantEnabled,canManage,generating,generationError,generate,save,feedback,onPending,onRegister,storageKey='',generationPhase='running',operationError='',publish}:{review:Row;checked:boolean;toggle:()=>void;busy:boolean;assistantEnabled:boolean;canManage:boolean;generating:boolean;generationError:string;generate:(instruction?:string)=>void;save:(text:string,revision:number)=>Promise<Row>;onPending?:(value:boolean)=>void;onRegister?:(id:string,flush:(()=>Promise<any>)|null)=>void|(()=>void);storageKey?:string;generationPhase?:string;operationError?:string;feedback:(rating:string,reasons:string[])=>void;publish?:()=>Promise<void>}){
  const [dirty,setDirty]=useState(false),[expanded,setExpanded]=useState(false),[opened,setOpened]=useState(false),[feedbackOpen,setFeedbackOpen]=useState(false),[feedbackReason,setFeedbackReason]=useState('');
  const flusher=useRef<(()=>Promise<any>)|null>(null);
  function register(id:string,flush:(()=>Promise<any>)|null){flusher.current=flush;return onRegister?.(id,flush)}
  async function preparePublication(){try{await flusher.current?.();await publish?.()}catch{/* DraftEditor or the parent shows the saving/publication error. */}}
  const q=r.draft?.quality||{},hasText=Boolean((r.text||'').trim());
  const needsCheck=r.rating<=3||r.manual||r.risk!=='NORMAL'||!q.passed||String(r.draft?.model||'').startsWith('safety-fallback');
  const status=r.is_answered?'Отправлено':generating?'Готовится':generationError?'Нужно повторить':!r.draft?'Нужен ответ':needsCheck?'Нужна проверка':'Готов к отправке';
  const reason=r.is_answered?'Ответ уже опубликован':generating?'Текущий текст сохраняется до завершения':generationError||(!r.draft?'Подготовьте черновик':r.rating<=3?'Низкая оценка: проверьте ответ перед отправкой':r.manual?'Опасный симптом: нужна личная проверка':q.issues?.join('; ')||'Перед отправкой проверьте текст');
  const blocked=busy||generating||!canManage||!assistantEnabled||dirty;
  const retryLabel=/ещё готовится|ожидание|задание сохранено/.test(generationError)?'Продолжить ожидание':'Повторить попытку';
  return <article className={'card review compact-review '+(r.rating<=3?'negative':'positive')}>
    <div className="review-head"><label className="check"><input type="checkbox" checked={checked} disabled={!canManage||r.is_answered} onChange={toggle}/><span className="review-product"><ProductThumbnail product={r.product}/><span className="review-product-copy"><b>{r.product.name}</b><span className="review-meta"><span>{r.product.brand} · {r.marketplace==='ozon'?'Ozon':r.marketplace==='yandex'?'Яндекс Маркет':'Wildberries'} {r.product_id}</span><span>{new Date(r.created_at).toLocaleDateString('ru-RU')}</span>{!hasText&&<span>Оценка без текста</span>}</span></span></span></label><div className="review-head-state"><span className="stars" aria-label={r.rating+' из 5'}>{'★'.repeat(r.rating)}<span>{'★'.repeat(5-r.rating)}</span></span><span role="status" title={reason} className={'pill '+(generating?'working':generationError||needsCheck?'amber':'green')}>{status}</span></div></div>
    <details className="review-expand" open={expanded||generating} onToggle={e=>{if(e.currentTarget.open)setOpened(true);if(!generating)setExpanded(e.currentTarget.open)}}><summary><span className="review-preview">{hasText?r.text:r.is_answered?'Ответ отправлен':'Покупатель поставил оценку без текста'}</span><span className="review-open-label">{expanded||generating?'Свернуть':'Открыть ответ'}</span></summary>
    {(opened||expanded||generating||Boolean(generationError))&&<div className={'review-content '+(hasText?'has-text':'rating-only')}>
      {hasText&&<div className="customer-review"><small>ОТЗЫВ ПОКУПАТЕЛЯ</small><p className="review-text">{r.text}</p></div>}
      {r.is_answered?<blockquote className="published-reply">{r.existing_answer}</blockquote>:<div className="draft">
        {r.draft?<DraftEditor draft={r.draft} disabled={generating||!canManage} storageKey={storageKey||'draft-edit:'+r.draft.id} onSave={save} onDirty={value=>{setDirty(value);onPending?.(value)}} onRegister={register}/>:<p className="muted">Подготовьте ответ на этот отзыв.</p>}
        <OwnerRuleStatus quality={q}/>
        {operationError&&<div className="save-error" role="alert">{operationError}</div>}
        {generationError&&<div className="generation-notice" role="status"><b>{generationError}</b>{r.draft&&<small>Предыдущий ответ сохранён. Вы можете продолжить работу с ним.</small>}</div>}
        
        <div className="draft-actions primary-actions"><button className={r.draft?'new-variant':'primary'} disabled={blocked} aria-busy={generating} title={dirty?'Сначала сохраните изменения':!assistantEnabled?'Ассистент выключен':!canManage?'Нет прав на подготовку ответов':''} onClick={()=>generate()}>{generating?'Готовим…':generationError?retryLabel:r.draft?'Новый вариант':'Подготовить ответ'}</button>{r.draft&&publish&&<button className="primary" disabled={busy||generating||!canManage} onClick={()=>void preparePublication()}>Подготовить публикацию</button>}{r.draft&&<div className="feedback-actions"><button className="subtle" disabled={busy||!canManage} onClick={()=>feedback('positive',[])}>👍 Хороший ответ</button><button className="subtle" disabled={busy||!canManage} onClick={()=>setFeedbackOpen(true)}>👎 Нужно исправить</button></div>}</div>
        {!assistantEnabled&&<small>Ассистент выключен владельцем.</small>}
        {generating&&<GenerationProgress phase={generationPhase}/>}
        {feedbackOpen&&<div className="inline-editor"><input autoFocus value={feedbackReason} onChange={e=>setFeedbackReason(e.target.value)} placeholder="Что исправить в ответе?"/><button disabled={busy||!feedbackReason.trim()} onClick={()=>{feedback('negative',[feedbackReason.trim()]);setFeedbackReason('');setFeedbackOpen(false)}}>Сохранить замечание</button><button onClick={()=>setFeedbackOpen(false)}>Отмена</button></div>}
        {r.draft&&<details className="reply-details"><summary>Проверка и история ответа</summary><p className="muted">{reason}</p>{q.issues?.length>0&&<p className="orange">{q.issues.join('; ')}</p>}<p className="muted">Оценка текста: {q.score??'—'}/100 · Подготовил: {r.draft.created_by_name||'Автоматизация'} · Последняя правка: {r.draft.updated_by_name||'Автоматизация'}</p>{q.analysis_summary&&<p>{q.analysis_summary}</p>}{q.selection_note&&<p>{q.selection_note}</p>}{q.sources?.length>0&&<details><summary>Источники ({q.sources.length})</summary>{q.sources.map((source:Row,i:number)=><p key={i}>{source.text}<small>Источник: {source.source}</small></p>)}</details>}{r.draft.edits?.length>0&&<details><summary>История правок ({r.draft.edits.length})</summary>{r.draft.edits.map((edit:Row,i:number)=><div key={i}><p className="muted">{edit.by_name||'Автоматизация'} · {edit.at?new Date(edit.at).toLocaleString('ru-RU'):''}</p>{edit.old&&<p><b>До:</b> {edit.old}</p>}{edit.new&&<p><b>После:</b> {edit.new}</p>}</div>)}</details>}{q.timing&&<p className="muted">Подготовка: {q.timing.total_seconds} сек. · {q.timing.route==='model'?'Модель':q.timing.route==='fallback'?'Резервный ответ':'Правила товара'}</p>}{q.owner_feedback&&<p>Ваша оценка: {q.owner_feedback.rating==='positive'?'ответ подходит':'нужно улучшить'}.</p>}</details>}
      </div>}
    </div>}</details>
  </article>;
}

