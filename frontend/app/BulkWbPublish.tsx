'use client';
import {useEffect,useRef,useState} from 'react';
import {api} from './client';
import ApprovalDialog from './ApprovalDialog';
import './wb-publication.css';

type Row=Record<string,any>;
const active=(status:string)=>['preparing','pending','sending'].includes(status);
const number=(value:number=0)=>value.toLocaleString('ru-RU');

export default function BulkWbPublish({storeId,canManage,busy,onChange,onReview}:{storeId:string;canManage:boolean;busy:boolean;onChange:()=>Promise<void>;onReview:(id:string)=>void}){
 const [includeExcluded,setIncludeExcluded]=useState(true),[excludedAck,setExcludedAck]=useState(false);
 const [data,setData]=useState<Row|null>(null),[open,setOpen]=useState(false),[working,setWorking]=useState(false),[error,setError]=useState(''),[ack,setAck]=useState(false);
 const [items,setItems]=useState<Row[]>([]),[offset,setOffset]=useState(0),[previewBusy,setPreviewBusy]=useState(false);
 const alive=useRef(true),serial=useRef(0),current=useRef({data,open});current.current={data,open};
 const path='/publications/wb/all',scope='store_id='+encodeURIComponent(storeId);
 async function load(){const version=serial.current;try{const result=await api(path+'?'+scope);if(alive.current&&version===serial.current){setData(result);setError('')}}catch(e){if(alive.current&&version===serial.current)setError(e instanceof Error?e.message:'Не удалось обновить отправку')}}
 useEffect(()=>{serial.current++;setOpen(false);setData(null);setWorking(false);setPreviewBusy(false);setAck(false);setExcludedAck(false);setError('')},[storeId]);
 useEffect(()=>{alive.current=true;if(canManage)void load();const timer=setInterval(()=>{if(canManage&&(current.current.open||active(current.current.data?.status)))void load()},3000);return()=>{alive.current=false;clearInterval(timer)}},[storeId,canManage]);
 async function preview(page:number){if(!data)return;const version=serial.current;setPreviewBusy(true);try{const result=await api(path+'/'+encodeURIComponent(data.id)+'/items?'+scope+'&offset='+page);if(alive.current&&version===serial.current){setItems(result.items);setOffset(page)}}catch(e){if(alive.current&&version===serial.current)setError(e instanceof Error?e.message:'Не удалось загрузить тексты')}finally{if(alive.current&&version===serial.current)setPreviewBusy(false)}}
 useEffect(()=>{setAck(false);setExcludedAck(false);setItems([]);setOffset(0);if(open&&data?.status==='pending')void preview(0)},[open,data?.id,data?.status]);
 async function action(operation:string){const version=++serial.current;setWorking(true);setError('');try{const result=await api(operation==='prepare'?path:path+'/'+encodeURIComponent(data!.id)+'/'+operation,'POST',{store_id:storeId,include_excluded:includeExcluded,excluded_ack:excludedAck});if(alive.current&&version===serial.current){setData(result);setAck(false);setExcludedAck(false)}if(operation!=='prepare')void onChange()}catch(e){if(alive.current&&version===serial.current)setError(e instanceof Error?e.message:'Не удалось выполнить действие')}finally{if(alive.current&&version===serial.current)setWorking(false)}}
 const pending=data?.status==='pending',sending=data?.status==='sending',preparing=data?.status==='preparing';
 return <>
  <button className="primary" disabled={busy||working||!canManage} onClick={()=>setOpen(true)}>{sending?'Отправка в WB: '+number(data?.published)+' / '+number(data?.total):preparing?'Собираем ответы для WB…':'Выгрузить ответы в WB'}</button>
  {open&&<ApprovalDialog title="Выгрузка в Wildberries" description="Сохранённые ответы со всех страниц магазина. Отправка — после вашего подтверждения." busy={working} error={error} onClose={()=>setOpen(false)}>
   <div className="wb-export">
   <div className="wb-export-destination"><span className="wb-export-logo">WB</span><div><strong>Ответы покупателям</strong><span>Без ожидания перегенерации</span></div></div>
   {(!data||!active(data.status))&&<>
    <fieldset className="wb-export-options"><legend>Какие ответы выгрузить</legend>
     <label className={includeExcluded?'selected':''}><input type="radio" name="wb-export-mode" checked={includeExcluded} onChange={()=>setIncludeExcluded(true)}/><span><strong>Все сохранённые, включая исключённые</strong><small>Низкие оценки и ответы без завершённой автопроверки. Подтверждает владелец.</small></span></label>
     <label className={!includeExcluded?'selected':''}><input type="radio" name="wb-export-mode" checked={!includeExcluded} onChange={()=>setIncludeExcluded(false)}/><span><strong>Только прошедшие автопроверку</strong><small>С актуальной проверкой правил и оценкой от четырёх звёзд.</small></span></label>
    </fieldset>
    <button className="primary wb-export-main" disabled={working} onClick={()=>void action('prepare')}>{working?'Собираем…':includeExcluded?'Собрать ответы с исключёнными':'Собрать проверенные ответы'}</button>
   </>}
   {data&&<>
    {(active(data.status)||['completed','needs_review'].includes(data.status))&&<div className="wb-export-stats"><div><span>В пакете</span><strong>{number(data.total)}</strong></div><div><span>{sending?'Опубликовано':'Из ранее исключённых'}</span><strong>{number(sending?data.published:data.included_excluded)}</strong></div><div><span>{sending?'Пропущено':'Не включены'}</span><strong>{number(sending?data.skipped:data.excluded)}</strong></div></div>}
    {preparing&&<p role="status">Проверяем черновики со всех страниц: просмотрено {number(data.scanned)}. Можно закрыть окно — подготовка продолжится на сервере.</p>}
    {data.blocked&&<p className="error">Реальная отправка заблокирована настройками безопасности или тестовым режимом. Подготовка списка ничего не публикует.</p>}
    {data.job_error&&<p className="error" role="alert">Подготовка или отправка остановлена: {data.job_error}</p>}
    {pending&&<>
     <div className="wb-export-notice">{data.include_excluded?'В пакет включены ответы без автоматического одобрения. Их подтверждение — ваше решение.':'Ответы прошли автоматическую проверку.'} Уже опубликованные ответы, начатые отправки и выявленные ошибки содержания не включаются.</div>
     <h4>Просмотр ответов · {offset+1}–{Math.min(offset+20,data.total)} из {number(data.total)}</h4>
     {previewBusy&&<p role="status">Загружаем тексты…</p>}
     <div className="wb-export-previews">{items.map(item=><article className="wb-export-reply" key={item.review_id}><header><span>{item.rating} ★</span>{item.owner_review&&<small>На решение владельца</small>}<button onClick={()=>{setOpen(false);onReview(item.review_id)}}>Открыть отзыв →</button></header><p>{item.text}</p></article>)}</div>
     <div className="button-row"><button disabled={previewBusy||offset===0} onClick={()=>void preview(Math.max(0,offset-20))}>Предыдущие 20</button><button disabled={previewBusy||offset+20>=data.total} onClick={()=>void preview(offset+20)}>Следующие 20</button></div>
     <label className="check"><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>Я проверил ответы и подтверждаю реальную отправку всех {number(data.total)} ответов в Wildberries.</label>
     {data.include_excluded&&<label className="check"><input type="checkbox" checked={excludedAck} onChange={e=>setExcludedAck(e.target.checked)}/>Как владелец подтверждаю тексты, включая низкие оценки и ответы без автоматического одобрения.</label>}
     <button className="primary wb-export-main" disabled={working||!ack||(data.include_excluded&&!excludedAck)||data.blocked||!items.length||previewBusy} onClick={()=>void action('confirm')}>Отправить {number(data.total)} ответов в WB</button>
    </>}
    {sending&&<><progress aria-label="Отправка ответов в Wildberries" max={Math.max(1,data.total)} value={data.processed}/><p role="status">Обработано {number(data.processed)} из {number(data.total)} · опубликовано {number(data.published)} · пропущено {number(data.skipped)} · нужна сверка {number(data.uncertain)}.</p><p className="muted">Отправка продолжится на сервере после закрытия сайта.</p></>}
    {['completed','needs_review'].includes(data.status)&&<p role="status">{data.status==='completed'?'Отправка завершена.':data.status==='empty'?'Проверенных ответов для массовой отправки сейчас нет.':data.status==='cancelled'?'Пакет остановлен.':'Пакет завершён; часть ответов требует отдельной проверки.'} Опубликовано: {number(data.published)}. Пропущено: {number(data.skipped)}. Нужна сверка: {number(data.uncertain)}.</p>}
    {active(data.status)&&<button disabled={working} onClick={()=>void action('cancel')}>{sending?'Остановить дальнейшую отправку':'Отменить пакет'}</button>}
   </>}
   </div>
  </ApprovalDialog>}
 </>;
}
