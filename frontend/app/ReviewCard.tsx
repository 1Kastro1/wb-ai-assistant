'use client';
import {useEffect,useState,useRef} from 'react';
type Row=Record<string,any>;

export function GenerationProgress(){
  const [seconds,setSeconds]=useState(0);
  useEffect(()=>{const start=Date.now();const timer=setInterval(()=>setSeconds(Math.floor((Date.now()-start)/1000)),1000);return()=>clearInterval(timer)},[]);
  return <div className="generation-progress" role="status" aria-live="polite"><div className="generation-spinner">✦</div><div><b>Готовим ответ · {seconds} сек.</b><p>{seconds<45?'Можно дождаться результата здесь.':'Подготовка занимает дольше обычного. Предыдущий текст сохраняется.'}</p></div></div>;
}

export default function ReviewCard({review:r,checked,toggle,busy,assistantEnabled,canManage,generating,generationError,generate,save,feedback}:{review:Row;checked:boolean;toggle:()=>void;busy:boolean;assistantEnabled:boolean;canManage:boolean;generating:boolean;generationError:string;generate:(instruction?:string)=>void;save:(text:string,revision:number)=>void;feedback:(rating:string,reasons:string[])=>void}){
  const [text,setText]=useState(r.draft?.text||''),[feedbackOpen,setFeedbackOpen]=useState(false),[feedbackReason,setFeedbackReason]=useState('');
  const editor=useRef<HTMLTextAreaElement|null>(null);
  useEffect(()=>setText(r.draft?.text||''),[r.draft?.text]);
  useEffect(()=>{const fit=()=>{const el=editor.current;if(el){el.style.height='auto';el.style.height=Math.min(360,Math.max(88,el.scrollHeight+4))+'px'}};fit();window.addEventListener('resize',fit);return()=>window.removeEventListener('resize',fit)},[text]);
  const q=r.draft?.quality||{},dirty=Boolean(r.draft&&text!==r.draft.text),hasText=Boolean((r.text||'').trim());
  const needsCheck=r.rating<=3||r.manual||r.risk!=='NORMAL'||!q.passed||String(r.draft?.model||'').startsWith('safety-fallback');
  const status=r.is_answered?'Отправлен':generating?'Генерируется':generationError?'Нужно повторить':!r.draft?'Нужен ответ':needsCheck?'Нужно проверить':'Готов к отправке';
  const reason=r.is_answered?'Ответ уже опубликован':generating?'Текущий текст сохраняется до завершения':generationError||(!r.draft?'Подготовьте черновик':r.rating<=3?'Низкая оценка: проверьте ответ перед отправкой':r.manual?'Опасный симптом: нужна личная проверка':q.issues?.join('; ')||'Перед отправкой проверьте текст');
  const blocked=busy||!canManage||!assistantEnabled||dirty;
  const retryLabel=/ещё готовится|ожидание|задание сохранено/.test(generationError)?'Продолжить ожидание':'Повторить попытку';
  return <article className={'card review compact-review '+(r.rating<=3?'negative':'positive')}>
    <div className="review-head"><label className="check"><input type="checkbox" checked={checked} disabled={!canManage||r.is_answered} onChange={toggle}/><b>{r.product.name}</b></label><div className="review-head-state"><span className="stars" aria-label={r.rating+' из 5'}>{'★'.repeat(r.rating)}<span>{'★'.repeat(5-r.rating)}</span></span><span role="status" title={reason} className={'pill '+(generating?'working':generationError||needsCheck?'amber':'green')}>{status}</span></div></div>
    <div className="review-meta"><span>{r.product.brand} · {r.marketplace==='ozon'?'Ozon':r.marketplace==='yandex'?'Яндекс Маркет':'Wildberries'} {r.product_id}</span><span>{new Date(r.created_at).toLocaleDateString('ru-RU')}</span>{!hasText&&<span>Оценка без текста</span>}</div>
    <div className={'review-content '+(hasText?'has-text':'rating-only')}>
      {hasText&&<div className="customer-review"><small>ОТЗЫВ ПОКУПАТЕЛЯ</small><p className="review-text">{r.text}</p></div>}
      {r.is_answered?<blockquote className="published-reply">{r.existing_answer}</blockquote>:<div className="draft">
        {r.draft?<label className="reply-editor">Ответ покупателю<textarea ref={editor} aria-label="Ответ покупателю" disabled={generating||!canManage} value={text} onChange={e=>setText(e.target.value)}/></label>:<p className="muted">Подготовьте ответ на этот отзыв.</p>}
        {generationError&&<div className="generation-notice" role="status"><b>{generationError}</b>{r.draft&&<small>Предыдущий ответ сохранён. Вы можете продолжить работу с ним.</small>}</div>}
        {dirty&&<small className="unsaved-note">Есть несохранённые изменения. Сохраните их перед генерацией нового варианта.</small>}
        <div className="draft-actions primary-actions">{r.draft&&<button className="primary" disabled={busy||!canManage||!dirty} onClick={()=>save(text,r.draft.revision)}>Сохранить</button>}<button className={r.draft?'new-variant':'primary'} disabled={blocked} aria-busy={generating} title={dirty?'Сначала сохраните изменения':!assistantEnabled?'Ассистент выключен':!canManage?'Нет прав на подготовку ответов':''} onClick={()=>generate()}>{generating?'Готовим…':generationError?retryLabel:r.draft?'Новый вариант':'Подготовить ответ'}</button>{r.draft&&<div className="feedback-actions"><button className="subtle" disabled={busy||!canManage} onClick={()=>feedback('positive',[])}>👍 Хороший ответ</button><button className="subtle" disabled={busy||!canManage} onClick={()=>setFeedbackOpen(true)}>👎 Нужно исправить</button></div>}</div>
        {!assistantEnabled&&<small>Ассистент выключен владельцем.</small>}
        {generating&&<GenerationProgress/>}
        {feedbackOpen&&<div className="inline-editor"><input autoFocus value={feedbackReason} onChange={e=>setFeedbackReason(e.target.value)} placeholder="Что исправить в ответе?"/><button disabled={busy||!feedbackReason.trim()} onClick={()=>{feedback('negative',[feedbackReason.trim()]);setFeedbackReason('');setFeedbackOpen(false)}}>Сохранить замечание</button><button onClick={()=>setFeedbackOpen(false)}>Отмена</button></div>}
        {r.draft&&<details className="reply-details"><summary>Проверка и история ответа</summary><p className="muted">{reason}</p>{q.issues?.length>0&&<p className="orange">{q.issues.join('; ')}</p>}<p className="muted">Оценка текста: {q.score??'—'}/100 · Подготовил: {r.draft.created_by_name||'Автоматизация'} · Последняя правка: {r.draft.updated_by_name||'Автоматизация'}</p>{q.analysis_summary&&<p>{q.analysis_summary}</p>}{q.selection_note&&<p>{q.selection_note}</p>}{q.sources?.length>0&&<details><summary>Источники ({q.sources.length})</summary>{q.sources.map((source:Row,i:number)=><p key={i}>{source.text}<small>Источник: {source.source}</small></p>)}</details>}{r.draft.edits?.length>0&&<details><summary>История правок ({r.draft.edits.length})</summary>{r.draft.edits.map((edit:Row,i:number)=><p key={i}>{edit.by_name||'Автоматизация'} · {edit.at?new Date(edit.at).toLocaleString('ru-RU'):''}</p>)}</details>}{q.owner_feedback&&<p>Ваша оценка: {q.owner_feedback.rating==='positive'?'ответ подходит':'нужно улучшить'}.</p>}</details>}
      </div>}
    </div>
  </article>;
}
