'use client';
import {useEffect,useState} from 'react';
import {batchProgress,batchWaitReason} from './batch-progress.mjs';
type Row=Record<string,any>;
export default function BatchDraftPanel({job,onCancel,onReview,busy=false,jobs=[]}:{jobs?:Row[];job:Row|null;onCancel:()=>void;onReview:()=>void;busy?:boolean}){
 const [clock,setClock]=useState(Date.now());
 const active=!!job&&['queued','running'].includes(job.status);
 useEffect(()=>{if(!active)return;const timer=setInterval(()=>setClock(Date.now()),1000);return()=>clearInterval(timer);},[active]);
 if(!job)return null;
 const result=job.result||{},finished=job.status==='completed',stopping=active&&job.cancel_requested;
 const waiting=batchWaitReason(job,jobs,clock);
 const progress=batchProgress(job),allReviews=!result.selected_ids?.length&&!result.since;
 return <section className="batch-draft-panel" aria-label="Подготовка ответов"><div className="card-heading"><div><h3>{stopping?'Останавливаем подготовку':job.status==='queued'?'Подготовка ответов в очереди':active?'Готовим ответы':finished?'Обработка завершена':'Подготовка остановлена'}</h3><p role="status" aria-live="polite">{progress.total==null?'Определяем количество отзывов':`Обработано ${progress.attempted} из ${progress.total} · ${progress.percent.toLocaleString('ru-RU')}% · осталось: ${progress.remaining}`} · сохранено ответов: {progress.processed}{progress.attention>0?` · на проверку: ${progress.attention}`:''}{progress.skipped>0?` · пропущено: ${progress.skipped}`:''}{progress.failed>0?` · технических ошибок: ${progress.failed}`:''}{result.retrying>0?` · ожидают повтора: ${result.retrying}`:''}</p><small>{allReviews?'Все отзывы выбранного магазина без ответа и черновика. Новые отзывы подхватываются во время обработки.':'Отзывы этого задания'}</small></div><div className="button-row">{active?<button disabled={busy||stopping} onClick={onCancel}>{stopping?'Останавливаем…':'Остановить подготовку'}</button>:<button onClick={onReview}>Просмотреть ответы</button>}</div></div><progress max={100} value={progress.percent} aria-label="Прогресс подготовки"/>
 {waiting&&<p role="status" className="muted">{waiting}</p>}
 {job.status==='running'&&(result.current_reviews||[]).map((item:Row)=><p className="muted" key={item.review_id}>{item.lane==='fast'?'Быстрый шаблон':'Подготовка и проверка ответа'}: отзыв {item.review_id} · {item.rating}★ · {new Date(item.created_at).toLocaleDateString('ru-RU')} · {Math.max(0,Math.floor((clock-Date.parse(item.started_at))/1000))} сек.</p>)}
 <p className="muted">Можно закрыть сайт — обработка продолжится на сервере. При возвращении здесь появится актуальный прогресс. Отправка покупателям — после Вашего подтверждения.</p>{result.message&&<p className="muted">{result.message}</p>}{result.failures?.length>0&&<details><summary>Последние ошибки и случаи для проверки ({result.failures.length})</summary>{result.failures.map((item:Row)=><p key={item.review_id}>{item.kind==='technical'?'Техническая ошибка: ':item.kind==='manual'?'Нужна проверка: ':''}{item.reason}</p>)}</details>}</section>;
}
