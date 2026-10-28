'use client';
import {useEffect,useState} from 'react';
import {batchProgress,batchWaitReason,batchStage} from './batch-progress.mjs';
type Row=Record<string,any>;
export default function BatchDraftPanel({job,onCancel,onReview,busy=false,jobs=[]}:{jobs?:Row[];job:Row|null;onCancel:()=>void;onReview:()=>void;busy?:boolean}){
 const [clock,setClock]=useState(Date.now());
 const active=!!job&&['queued','running'].includes(job.status);
 useEffect(()=>{if(!active)return;const timer=setInterval(()=>setClock(Date.now()),1000);return()=>clearInterval(timer);},[active]);
 if(!job)return null;
 const result=job.result||{},finished=job.status==='completed',stopping=active&&job.cancel_requested;
 const waiting=batchWaitReason(job,jobs,clock),progress=batchProgress(job);
 const allReviews=!result.selected_ids?.length&&!result.since;
 const number=(n:number)=>n.toLocaleString('ru-RU');
 return <section className={'batch-draft-panel batch-workspace '+(active?'is-active':'is-terminal')} aria-label="Подготовка ответов">
  <div className="batch-overview"><span className={'batch-state-icon '+(finished?'complete':active?'working':'stopped')} aria-hidden="true">{finished?'✓':active?'◷':'Ⅱ'}</span><div className="batch-overview-copy"><h3>{batchStage(job)}</h3><p role="status" aria-live="polite">Сохранено ответов: {number(progress.processed)}{progress.total!=null?` · обработано ${number(progress.attempted)} из ${number(progress.total)}`:''}</p></div><div className="batch-outcomes">{progress.attention>0&&<span className="batch-attention">На проверку: {number(progress.attention)}</span>}{progress.failed>0&&<span className="batch-attention">Ошибок: {number(progress.failed)}</span>}</div><div className="batch-controls">{active?<button disabled={busy||stopping} onClick={onCancel}>{stopping?'Останавливаем…':'Остановить подготовку'}</button>:<button onClick={onReview}>Просмотреть ответы</button>}</div></div>
  {active&&<div className="batch-progress-line"><progress max={100} value={progress.percent} aria-label="Прогресс подготовки"/><span>{progress.percent.toLocaleString('ru-RU')}%</span></div>}
  {waiting&&<p role="status" className="muted batch-wait">{waiting}</p>}
  {active&&job.status==='running'&&(result.current_reviews||[]).map((item:Row)=><p className="muted batch-current" key={item.review_id}>{item.lane==='fast'?'Быстрый шаблон':'Подготовка и проверка ответа'}: {item.rating}★ · {new Date(item.created_at).toLocaleDateString('ru-RU')} · {Math.max(0,Math.floor((clock-Date.parse(item.started_at))/1000))} сек.</p>)}
  <details className="batch-details" key={job.id}><summary>Подробности задания</summary><div>
   <p>{progress.total==null?'Количество отзывов ещё определяется':`Обработано ${number(progress.attempted)} из ${number(progress.total)} · осталось: ${number(progress.remaining||0)}`} · сохранено: {number(progress.processed)}{progress.skipped>0?` · пропущено: ${number(progress.skipped)}`:''}{result.retrying>0?` · ожидают повтора: ${number(result.retrying)}`:''}</p>
   <p className="muted">{allReviews?'Все отзывы выбранного магазина без ответа и черновика.':'Отзывы этого задания.'} {active?'Можно закрыть сайт — обработка продолжится на сервере.':'Сохранённые ответы доступны для просмотра.'} Отправка покупателям — после Вашего подтверждения.</p>
   {result.message&&<p className="muted">{result.message}</p>}
   {result.failures?.length>0&&<details><summary>Ошибки и случаи для проверки ({result.failures.length})</summary>{result.failures.map((item:Row,index:number)=><p key={item.review_id||index}>{item.kind==='technical'?'Техническая ошибка: ':item.kind==='manual'?'Нужна проверка: ':''}{item.reason}</p>)}</details>}
  </div></details>
 </section>;
}
