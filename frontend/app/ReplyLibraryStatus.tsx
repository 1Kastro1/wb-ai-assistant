'use client';
import {useEffect,useState} from 'react';
import {api} from './client';
type Row=Record<string,any>;
const markets:Record<string,string>={yandex:'Яндекс Маркет',wb:'Wildberries',ozon:'Ozon'};
export default function ReplyLibraryStatus(){
 const [data,setData]=useState<Row|null>(null),[error,setError]=useState('');
 useEffect(()=>{let active=true;let timer:ReturnType<typeof setTimeout>;
  async function load(){try{const result=await api('/review-library/status');if(active){setData(result);setError('')}}catch{if(active)setError('Не удалось обновить состояние проверки.')}finally{if(active)timer=setTimeout(load,15000)}}
  void load();return()=>{active=false;clearTimeout(timer)};
 },[]);
 if(!data?.job&&!error)return null;
 const job=data?.job,stages:Row[]=job?.stages||[];
 return <section className="card" aria-label="Библиотека проверенных ответов">
  <h3>Библиотека проверенных ответов</h3>
  {error&&<p role="status" className="muted">{error}</p>}
  {job&&<><p>{job.status==='completed'?'Проверка завершена':job.status==='failed'?'Проверка требует внимания':job.status==='cancelled'?'Проверка остановлена':'Проверка по очереди: Яндекс Маркет → Wildberries → Ozon'}</p>
   <p className="muted">{job.message}</p>
   <progress max={Math.max(1,job.total||0)} value={job.checked||0} aria-label="Проверено отзывов" style={{width:'100%'}}/>
   <div className="button-row"><span className="pill">Уникальных текстов: {data?.library?.unique_templates||0}</span><span className="pill">Прошли проверку: {job.verified||0}</span><span className="pill">Требуют внимания: {job.attention||0}</span></div>
   {job.automated_repair&&<p className="muted">Замечания к обычным черновикам исправляются автоматически с повторной проверкой. Для опубликованных ответов при необходимости готовится отдельный проверенный образец; опубликованный текст сохраняется. Таких образцов: {job.corrected_references||0}.</p>}
   <table><thead><tr><th>Маркетплейс</th><th>Проверено</th><th>Подтверждено</th><th>Состояние</th></tr></thead><tbody>{stages.map((stage,index)=><tr key={stage.account_id}><td>{markets[stage.marketplace]||stage.marketplace}</td><td>{stage.checked||0} / {stage.total||0}</td><td>{stage.verified||0}</td><td>{stage.completed_at?'Завершено':index===job.stage?'Текущий этап':'Ожидает'}</td></tr>)}</tbody></table>
   {job.detail&&<p role="status">{job.detail}</p>}
   <p className="muted">Проверенные тексты хранятся без дублей. Для похожего отзыва с другими обстоятельствами правила проверяются заново. Обновление и отправка остаются выключены.</p>
  </>}
 </section>;
}
