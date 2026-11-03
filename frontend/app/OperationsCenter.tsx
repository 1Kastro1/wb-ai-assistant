'use client';
import {useEffect,useState} from 'react';
import {api} from './client';
import {createBackgroundPoller} from './background-work.mjs';
import {serverWorkGroups,workStages} from './server-work.mjs';
import './server-work.css';
type Row=Record<string,any>;
const providers:Row={wb:'Wildberries',yandex:'Яндекс Маркет',ozon:'Ozon'};
const marks:Row={wb:'W',yandex:'Я',ozon:'O'};
function time(value:string){return value?new Date(value).toLocaleString('ru-RU'):'—'}
export default function OperationsCenter({initial,stale,lastSync,onConnections}:{initial:Row;stale:boolean;lastSync:Date|null;onConnections:()=>void}){
 const [data,setData]=useState(initial),[error,setError]=useState(''),[page,setPage]=useState(0);
 useEffect(()=>{setData(initial)},[initial]);
 useEffect(()=>{const poller=createBackgroundPoller({load:()=>api('/system/status'),onValue:(value:Row)=>{setData(value);setError('')},onError:()=>setError('Не удалось обновить состояние сервера. Показаны последние полученные данные.'),isVisible:()=>!document.hidden,idleDelay:15000,errorDelay:30000});const wake=()=>poller.wake();document.addEventListener('visibilitychange',wake);poller.start();return()=>{document.removeEventListener('visibilitychange',wake);poller.stop()}},[]);
 const known=Boolean(data.observed_at),synced=data.latest?.reviews,currentSync=synced?new Date(synced.result?.finished_at||synced.created_at):lastSync;
 const outdated=currentSync?Date.now()-currentSync.getTime()>24*3600*1000:stale;
 const attention=known&&(outdated||data.needs_attention||data.database_health?.status==='failed'),tasks=data.tasks||[],groups=serverWorkGroups(tasks);
 const totalPages=Math.max(1,Math.ceil(tasks.length/5)),currentPage=Math.min(page,totalPages-1);
 return <div className="card operations-center server-work" aria-label="Работа на сервере">
  <div className="card-heading"><div><p className="work-eyebrow">ЕДИНЫЙ ПРОЦЕСС ДЛЯ КАЖДОГО МАРКЕТА</p><h3>Работа на сервере</h3><p className="muted">Обновляем отзывы, готовим ответы, проверяем и передаём в кабинеты. Можно закрыть сайт.</p></div><span className={'pill '+(attention?'amber':known?'green':'')}>{!known?'Подключаемся…':attention?'Есть вопросы для проверки':data.running_jobs?'Работает в фоне':tasks.length?'Ожидает запуска':'Нет активных процессов'}</span></div>
  {error&&<p role="status" className="orange">{error}</p>}
  <div className="market-processes">{groups.map((group:Row)=><article className="market-process" key={group.id}>
   <div className="market-process-title"><span className={'process-mark '+group.provider} aria-hidden="true">{marks[group.provider]||'↻'}</span><div><b>{providers[group.provider]||'Сервер'}</b><small>{group.name}</small></div><span className="work-live-dot" aria-label={group.tasks.some((t:Row)=>t.status==='running')?'Выполняется':'В очереди'}/></div>
   <div className="process-stages">{workStages.map(([key,label]:string[])=>{const s=group.stages[key];const status=s?.running?'Выполняется':s?.queued?'В очереди':s?.retry?'Повторим автоматически':'Нет активных задач';return <div className={s?.running?'is-running':s?'is-waiting':''} key={key}><span className="process-stage-dot"/><div><b>{label}</b><small>{status}</small></div></div>})}</div>
   <p className="process-caption">{group.tasks.some((t:Row)=>t.status==='running')?'Прогресс сохраняется на сервере':'Продолжим с сохранённого места по очереди'}</p>
  </article>)}</div>
  {known&&!groups.length&&<p className="work-idle">Текущая очередь пуста. Новые отзывы поступят по сохранённому расписанию.</p>}
  <div className="work-summary"><p>{data.generation_window?<>За {data.generation_window.window_minutes} мин. в выбранном магазине: <b>{data.generation_window.saved} ответов создано</b>, {data.generation_window.verified} прошли проверку.</>:known?'Прогресс обновляется автоматически.':'Получаем прогресс…'}</p><p>Расписание: {!known?'проверяем…':data.scheduler?.state==='error'?'нужна проверка':data.automation_enabled?'включено':'выключено'}. Обновление отзывов: {known?(currentSync?.toLocaleString('ru-RU')||'ожидаем синхронизацию'):'проверяем…'}.</p></div>
  {(data.sync_incidents||[]).map((incident:Row)=><p className="orange" key={incident.kind}>{incident.message}</p>)}
  {attention&&<button onClick={onConnections}>Открыть подключения и синхронизацию →</button>}
  {tasks.length>0&&<details className="work-technical"><summary>Технические подробности</summary><p className="muted">Выполняются: {data.running_jobs||0} · в очереди: {data.queued_jobs||0} · отложены на повтор: {data.retry_jobs||0}</p><div className="work-technical-list">{tasks.slice(currentPage*5,currentPage*5+5).map((task:Row)=><div key={task.id}><b>{task.label}</b><small>{task.store_name} · {task.status==='running'?'Выполняется':'В очереди'}</small><p>{task.message||task.reason}</p><small>Обработано: {task.processed||0}{task.total?' из '+task.total:''} · обновлено {time(task.updated_at)}</small></div>)}</div>{totalPages>1&&<div className="work-pagination"><button disabled={!currentPage} onClick={()=>setPage(currentPage-1)}>Назад</button><small>{currentPage+1} / {totalPages}</small><button disabled={currentPage>=totalPages-1} onClick={()=>setPage(currentPage+1)}>Далее</button></div>}</details>}
  {data.server_backup&&<details><summary>Резервная копия сервера</summary><p>{time(data.server_backup.created_at)} · {data.server_backup.size_mb} МБ · {data.server_backup.catalog_verified?'структура проверена':'ожидает проверки'}</p></details>}
  <small className="work-observed">Состояние получено: {known?time(data.observed_at):'загрузка…'}</small>
 </div>;
}
