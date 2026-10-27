'use client';
import {useEffect,useRef,useState} from 'react';
import {api} from './client';

type Row=Record<string,any>;
const n=(value:number=0)=>value.toLocaleString('ru-RU');
export default function BulkExcluded({parentId,storeId,onReady}:{parentId:string;storeId:string;onReview:(id:string)=>void;onReady:()=>void}){
 const [job,setJob]=useState<Row|null>(null),[loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const alive=useRef(true),serial=useRef(0),acting=useRef(false);
 const path='/publications/wb/all/'+encodeURIComponent(parentId)+'/exclusions';
 async function load(){const version=serial.current;try{const result=await api(path+'/summary?store_id='+encodeURIComponent(storeId));if(alive.current&&version===serial.current){setJob(result.recovery);setError('')}}catch(e){if(alive.current&&version===serial.current)setError(e instanceof Error?e.message:'Не удалось получить прогресс')}finally{if(alive.current)setLoading(false)}}
 useEffect(()=>{alive.current=true;void load();const timer=setInterval(()=>{if(!acting.current)void load()},5000);return()=>{alive.current=false;serial.current++;clearInterval(timer)}},[parentId,storeId]);
 async function action(stop=false,retry=false){acting.current=true;serial.current++;setBusy(true);setError('');try{const result=await api(path+'/recovery'+(stop?'/stop':''),'POST',{store_id:storeId,...(!stop?{retry_errors:retry}:{})},60000);if(alive.current)setJob(result)}catch(e){if(alive.current)setError(e instanceof Error?e.message:'Не удалось запустить подготовку')}finally{acting.current=false;if(alive.current)setBusy(false)}}
 const active=job&&['queued','running'].includes(job.status),finished=job?.status==='completed',paused=job&&['cancelled','failed','interrupted'].includes(job.status);
 return <section aria-label="Новые ответы на исключённые отзывы" className="notice" style={{padding:24,borderRadius:20,marginTop:20}}>
  <h3>Подготовить исключённые отзывы заново</h3>
  <p>Новые ответы будут созданы по актуальным правилам и проверены перед выгрузкой в Wildberries. Работа продолжится на сервере, даже если закрыть сайт.</p>
  <p className="muted">Ручные правки сохраняются. Низкие оценки и опасные ситуации остаются на личной проверке. Подготовка ничего не публикует.</p>
  {loading&&<p role="status">Загружаем состояние подготовки…</p>}
  {error&&<p role="alert" className="error">{error}</p>}
  {job&&<>
   <div className="metrics"><div className="metric"><span>Обработано</span><strong>{n(job.processed)} / {n(job.total)}</strong></div><div className="metric"><span>Готовы к выгрузке</span><strong>{n(job.ready)}</strong></div><div className="metric"><span>Нужна личная проверка</span><strong>{n(job.manual)}</strong></div></div>
   <progress aria-label="Подготовка исключённых отзывов" max={Math.max(1,job.total)} value={job.processed} style={{width:'100%'}}/>
   <p role="status">{finished?'Подготовка завершена.':paused?'Подготовка остановлена; прогресс сохранён.':job.status==='queued'?'Подготовка в очереди.':'Готовим и проверяем ответы.'} {job.retrying?'Повторяем проверку после временного сбоя.':''}</p>
   {!!job.errors&&<p>Не удалось завершить: {n(job.errors)}. Их можно повторить отдельно; готовые ответы сохраняются.</p>}
   {job.detail&&<p className="muted">{job.detail}</p>}
  </>}
  <div className="button-row" style={{flexWrap:'wrap'}}>
   {!loading&&!active&&<button className="primary" disabled={busy} onClick={()=>void action()}>{busy?'Запускаем…':paused?'Продолжить подготовку':'Перегенерировать исключённые отзывы'}</button>}
   {active&&<button disabled={busy||job.cancel_requested} onClick={()=>void action(true)}>{job.cancel_requested?'Останавливаем после текущего ответа…':'Остановить подготовку'}</button>}
   {!active&&!!job?.errors&&<button disabled={busy} onClick={()=>void action(false,true)}>Повторить ошибки подготовки ({n(job.errors)})</button>}
   {!active&&!!job?.ready&&<button className="primary" disabled={busy} onClick={onReady}>Собрать готовые ответы для выгрузки в WB</button>}
  </div>
  {!!job?.ready&&<p className="muted">Счётчики показывают результат подготовки. Перед отправкой список будет собран заново с проверкой актуального текста и правил; публикация потребует Вашего подтверждения.</p>}
 </section>;
}
