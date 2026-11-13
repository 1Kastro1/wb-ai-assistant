'use client';
import {useEffect,useState} from 'react';
import {api} from './client';
import './reply-library.css';
import ReviewPolicyCampaign from './ReviewPolicyCampaign';
type Row=Record<string,any>;
const markets:Record<string,string>={yandex:'Яндекс Маркет',wb:'Wildberries',ozon:'Ozon'};
const statuses:Record<string,string>={verified:'Подтверждён',verified_reference:'Проверенный образец',attention:'Нужно решение',retry:'Запланирован повтор',blocked:'Нужна настройка сервиса'};
const number=(value:any)=>Number(value||0).toLocaleString('ru-RU');
export default function ReplyLibraryStatus({onRule,onOpen}:{onRule?:(rule:Row)=>void;onOpen?:(item:Row)=>void}){
 const [data,setData]=useState<Row|null>(null),[error,setError]=useState(''),[tab,setTab]=useState('overview');
 const [rows,setRows]=useState<Row[]>([]),[total,setTotal]=useState(0),[offset,setOffset]=useState(0),[loading,setLoading]=useState(false),[listError,setListError]=useState('');
 useEffect(()=>{let active=true;let timer:ReturnType<typeof setTimeout>;
  async function load(){try{const result=await api('/review-library/status','GET',undefined,15000);if(active){setData(result);setError('')}}catch{if(active)setError('Не удалось обновить библиотеку. Показаны последние данные.')}finally{if(active)timer=setTimeout(load,document.hidden?30000:15000)}}
  void load();return()=>{active=false;clearTimeout(timer)};
 },[]);
 useEffect(()=>{if(tab==='overview')return;let active=true;setLoading(true);setListError('');
  api('/review-library/cases?view='+tab+'&offset='+offset+'&limit=20','GET',undefined,15000).then(result=>{if(active){setRows(result.items);setTotal(result.total)}}).catch(()=>{if(active)setListError('Не удалось загрузить случаи. Переключите вкладку для повтора.')}).finally(()=>{if(active)setLoading(false)});
  return()=>{active=false};
 },[tab,offset,data?.counts?.checked,data?.counts?.retry]);
 const job=data?.job,stages:Row[]=job?.stages||[],counts=data?.counts||{},stage=stages[job?.stage];
 const percent=stage?.total?Math.min(100,100*stage.checked/stage.total):job?.status==='completed'?100:0;
 return <section className="reply-library" aria-label="Библиотека ответов">
  <div className="section-title"><div><p className="eyebrow">ПРОВЕРЕННЫЕ ОТВЕТЫ И СЛОЖНЫЕ СЛУЧАИ</p><h1>Библиотека ответов</h1><p className="muted">Сохранённые образцы ускоряют ответы в том же контексте и по тем же правилам.</p></div><span className="pill">{job?.status==='completed'?'Завершено':job?.status==='failed'?'Нужна проверка':job?.status==='cancelled'?'Остановлено':job?'Обработка продолжается':'Нет активной проверки'}</span></div>
  {error&&<p role="status" className="orange">{error}</p>}
  <div className="segmented library-tabs" role="tablist" aria-label="Разделы библиотеки">{[['overview','Обзор'],['attention','Требуют внимания'],['retry','Повторы'],['history','История']].map(([id,label])=><button key={id} id={'library-tab-'+id} role="tab" aria-selected={tab===id} aria-controls="library-panel" onClick={()=>{setTab(id);setOffset(0)}}>{label}</button>)}</div>
  <div id="library-panel" role="tabpanel" aria-labelledby={'library-tab-'+tab}>
   {tab==='overview'?<>
    {!data?<p role="status">Загружаем библиотеку…</p>:<>
     <div className="library-metrics">{[['Уникальные образцы',data.library?.unique_templates],['Подтверждённые отзывы',counts.verified],['Требуют внимания',counts.attention],['Ожидают повтора',(counts.retry||0)+(counts.blocked||0)]].map(([label,value])=><div className="card" key={label}><small>{label}</small><strong>{number(value)}</strong></div>)}</div>
     {job&&<div className="card library-progress"><div><h3>{stage?markets[stage.marketplace]:'Проверка всех магазинов'}</h3><b>{percent.toLocaleString('ru-RU',{maximumFractionDigits:1})}%</b></div><progress max={100} value={percent} aria-label="Прогресс текущего магазина"/><p className="muted">Подтверждено — ответ прошёл проверки. Остальные проверенные случаи находятся во вкладке «Требуют внимания»; технические сбои — в «Повторах».</p>
      <div className="table-wrap"><table><thead><tr><th>Маркетплейс</th><th>Проверено / всего</th><th>Подтверждено</th><th>Состояние</th></tr></thead><tbody>{stages.map((item,index)=><tr key={item.account_id}><td>{markets[item.marketplace]||item.marketplace}</td><td>{number(item.checked)} / {number(item.total)}</td><td>{number(item.verified)}</td><td>{item.completed_at?'Завершено':index===job.stage?'Проверяется':'В очереди'}</td></tr>)}</tbody></table></div>
     </div>}
     <details className="card library-help"><summary>Как библиотека ускоряет ответы</summary><p>Один текст хранится один раз. У каждого его применения есть отдельная проверка контекста: товар, отзыв, оценка, факты и правила магазина.</p><p>По готовому образцу обработано: {number(job?.template_reused)}. Исправленных образцов для опубликованных ответов: {number(job?.corrected_references)}.</p><p>Опубликованные тексты сохраняются. Проекты правил для сложных случаев создаются отдельно и не означают, что ответ уже проверен.</p></details>
     <p className="muted">{data.safe_mode?'Отправка ответов заблокирована. ':''}{data.maintenance?'Идёт проверка библиотеки; обычное расписание приостановлено.':'Режим отправки управляется в настройках автоматизации.'}</p>
    </>}
   </>:<>
    <p className="muted">{tab==='attention'?'Здесь указана причина каждого неподтверждённого ответа. Проект правила помогает разобрать ситуацию; ответ должен пройти повторную проверку.':tab==='retry'?'Техническая ошибка не является нарушением правил. Новое правило не устраняет тайм-аут или недоступность модели.':'Результаты проверки отзывов. Опубликованные ответы сохраняются.'}</p>
    {loading&&<p role="status">Обновляем список…</p>}{listError&&<p role="alert">{listError}</p>}
    {!loading&&!listError&&!rows.length&&<div className="empty">В этом разделе пока нет случаев.</div>}
    {!listError&&rows.map(item=><article className="card library-case" key={item.review_id}>
     <div className="card-heading"><small>{markets[item.marketplace]} · {item.rating} ★</small><span className="pill">{statuses[item.status]||item.status}</span></div><h3>{item.product}</h3><blockquote>{item.review_text||'Оценка без текста'}</blockquote>
     {item.issues?.length>0&&<ul className="library-issues">{item.issues.map((issue:string,index:number)=><li key={index}>{issue}</li>)}</ul>}
     {item.category==='publication'&&<p>Нужно сверить результат с маркетплейсом. Новое правило текста здесь не требуется.</p>}
     {item.suggestion&&<div className="library-suggestion"><b>{item.suggestion.enabled?'Правило включено':'Создан проект правила'}</b><p>{item.suggestion.text}</p>{onRule&&<button onClick={()=>onRule(item.suggestion)}>Открыть в правилах</button>}</div>}
     <details><summary>Ответ и история проверки</summary><p className="library-answer">{item.answer||'Текст ответа отсутствует'}</p><small>Попыток: {item.attempts} · {new Date(item.updated_at).toLocaleString('ru-RU')}{item.retry_at>0?' · повтор не раньше '+new Date(item.retry_at*1000).toLocaleString('ru-RU'):''}</small></details>
     {onOpen&&<button onClick={()=>onOpen(item)}>Открыть отзыв →</button>}
    </article>)}
    {total>20&&<div className="button-row"><button disabled={loading||offset===0} onClick={()=>setOffset(Math.max(0,offset-20))}>Назад</button><span>{offset+1}–{Math.min(offset+20,total)} из {number(total)}</span><button disabled={loading||offset+20>=total} onClick={()=>setOffset(offset+20)}>Далее</button></div>}
   </>}
  </div>
  <details className="library-help card"><summary>Обновление сохранённых ответов по правилам</summary><ReviewPolicyCampaign/></details>
 </section>;
}
