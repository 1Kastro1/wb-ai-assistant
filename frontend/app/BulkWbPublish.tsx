'use client';
import {useEffect,useRef,useState} from 'react';
import {api} from './client';
import ApprovalDialog from './ApprovalDialog';
import PublicationChoices from './PublicationChoices';
import {publicationDestination} from './publication-scope.mjs';
import './wb-publication.css';

type Row=Record<string,any>;
type Scope='all'|'positive';
const scopes:Scope[]=['all','positive'];
const active=(status:string)=>['preparing','pending','sending'].includes(status);
const number=(value:number=0)=>value.toLocaleString('ru-RU');
type Props={storeId:string;provider:string;canManage:boolean;busy:boolean;onChange:()=>Promise<void>};

export default function BulkWbPublish({storeId,provider,canManage,busy,onChange}:Props){
 const [open,setOpen]=useState(false),[statuses,setStatuses]=useState<Record<string,Row|null>>({});
 const [working,setWorking]=useState<string|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);
 const alive=useRef(true),serial=useRef(0),reading=useRef(false),current=useRef({open,statuses});current.current={open,statuses};
 const destination=publicationDestination(provider),path='/publications/reviews/all';
 async function load(){
  if(reading.current)return;reading.current=true;const version=serial.current;
  try{const values=await Promise.all(scopes.map(scope=>api(path+'?store_id='+encodeURIComponent(storeId)+'&rating_scope='+scope)));
   if(alive.current&&version===serial.current){setStatuses({all:values[0],positive:values[1]});setLoading(false)}
  }catch(e){if(alive.current&&version===serial.current){setLoading(false);setError(e instanceof Error?e.message:'Не удалось получить состояние выгрузки')}}
  finally{reading.current=false}
 }
 useEffect(()=>{alive.current=true;serial.current++;if(canManage)void load();const refresh=()=>{if(document.visibilityState==='visible'&&canManage&&(current.current.open||Object.values(current.current.statuses).some(row=>active(row?.status))))void load()};
  const timer=setInterval(refresh,3000);document.addEventListener('visibilitychange',refresh);
  return()=>{alive.current=false;serial.current++;clearInterval(timer);document.removeEventListener('visibilitychange',refresh)};
 },[storeId,canManage]);
 async function launch(scope:Scope){
  const version=++serial.current;setWorking(scope);setError('');
  try{const result=await api(path+'/launch','POST',{store_id:storeId,rating_scope:scope});if(alive.current&&version===serial.current)setStatuses(previous=>({...previous,[scope]:result}));void onChange().catch(()=>{})}
  catch(e){if(alive.current&&version===serial.current){setError(e instanceof Error?e.message:'Не удалось запустить выгрузку');void load()}}
  finally{if(alive.current&&version===serial.current)setWorking(null)}
 }
 async function stop(scope:Scope,row:Row){
  const version=++serial.current;setWorking('stop:'+scope);setError('');
  try{const result=await api(path+'/'+encodeURIComponent(row.id)+'/cancel','POST',{store_id:storeId,rating_scope:scope});if(alive.current&&version===serial.current)setStatuses(previous=>({...previous,[scope]:result}));void onChange().catch(()=>{})}
  catch(e){if(alive.current&&version===serial.current)setError(e instanceof Error?e.message:'Не удалось остановить выгрузку')}
  finally{if(alive.current&&version===serial.current)setWorking(null)}
 }
 const running=Object.values(statuses).some(row=>active(row?.status));
 return <>
  <button className="primary export-trigger" disabled={busy||!canManage} onClick={()=>{setOpen(true);void load()}}>{running&&<span className="export-live-dot" aria-hidden="true"/>}Выгрузить положительные отзывы</button>
  {open&&<ApprovalDialog title="Выгрузка ответов" description="" busy={Boolean(working)} error={error} onClose={()=>setOpen(false)}>
   <div className="review-export" data-provider={provider}>
    <div className="export-store"><span className="export-brand">{destination.logo}</span><div><strong>{destination.name}</strong><span>Готовые ответы выбранного магазина</span></div><span className="export-saved">Тексты сохранены</span></div>
    <PublicationChoices loading={loading} working={working} disabled={!canManage||busy} statuses={statuses} onLaunch={scope=>void launch(scope)}/>
    <p className="export-consent">Нажатие запускает сборку и реальную отправку ответов в {destination.name}. Уже опубликованные ответы не отправляются повторно.</p>
    <p className="export-personal">Оценки 1–3 остаются для вашей личной проверки и отдельного подтверждения. Ошибки содержания и уже начатые публикации не включаются.</p>
    <div className="export-jobs">{scopes.map(scope=>{
     const row=statuses[scope];if(!row)return null;
     const title=scope==='positive'?'Положительные отзывы':'Все отзывы, включая исключённые';
     const preparing=row.status==='preparing',sending=row.status==='sending';
     if(!active(row.status))return <div className="export-last" key={row.id}><span>{title}</span><strong>{row.status==='empty'?'Нет ответов для отправки':row.status==='cancelled'?'Остановлено':row.status==='expired'?'Список обновляется':row.status==='completed'?'Завершено':'Нужна проверка'} · отправлено {number(row.published)}</strong></div>;
     return <section className="export-job" key={row.id} aria-label={title}>
      <div className="export-job-heading"><h3>{title}</h3><span className={'export-state '+(sending?'sending':'')}>{sending?'Отправляем':preparing?'Собираем':row.auto_send_error?'Нужна проверка':'Список готов'}</span></div>
      <div className="export-stages" aria-label="Этапы выгрузки"><span className={preparing?'current':'done'}>1 · Сборка</span><i/><span className={sending?'current':''}>2 · Отправка в {destination.short}</span></div>
      {preparing?<div className="export-progress is-gathering" aria-label="Сборка списка"><i/></div>:<progress aria-label="Отправка ответов" max={Math.max(1,row.total)} value={row.processed}/>}
      <div className="export-job-numbers"><strong>{sending?number(row.published)+' отправлено':number(row.total)+' в списке'}</strong><span>{preparing?'Просмотрено '+number(row.scanned):number(row.processed)+' из '+number(row.total)+' обработано'}</span></div>
      {(row.uncertain>0||row.skipped>0)&&<p className="export-attention">Пропущено: {number(row.skipped)} · требуют сверки: {number(row.uncertain)}</p>}
      {row.auto_send_error&&<p className="error" role="alert">Отправка не началась: {row.auto_send_error}</p>}
      {row.job_error&&<p className="error" role="alert">{row.job_error}</p>}
      {row.blocked&&<p className="error">Отправка заблокирована настройками рабочего режима.</p>}
      <div className="export-job-footer"><span>{row.auto_send||sending?'Можно закрыть окно — работа продолжится.':'Для отправки выберите вариант выше.'}</span><button disabled={Boolean(working)} onClick={()=>void stop(scope,row)}>{working==='stop:'+scope?'Останавливаем…':'Остановить'}</button></div>
     </section>;
    })}</div>
   </div>
  </ApprovalDialog>}
 </>;
}

