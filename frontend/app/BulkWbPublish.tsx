'use client';
import {useEffect,useRef,useState} from 'react';
import {api} from './client';
import ApprovalDialog from './ApprovalDialog';
import BulkExcluded from './BulkExcluded';

type Row=Record<string,any>;
const active=(status:string)=>['preparing','pending','sending'].includes(status);
const number=(value:number=0)=>value.toLocaleString('ru-RU');

export default function BulkWbPublish({storeId,canManage,busy,onChange,onReview}:{storeId:string;canManage:boolean;busy:boolean;onChange:()=>Promise<void>;onReview:(id:string)=>void}){
 const [excludedOpen,setExcludedOpen]=useState(false);
 const [data,setData]=useState<Row|null>(null),[open,setOpen]=useState(false),[working,setWorking]=useState(false),[error,setError]=useState(''),[ack,setAck]=useState(false);
 const [items,setItems]=useState<Row[]>([]),[offset,setOffset]=useState(0),[previewBusy,setPreviewBusy]=useState(false);
 const alive=useRef(true),serial=useRef(0),current=useRef({data,open});current.current={data,open};
 const path='/publications/wb/all',scope='store_id='+encodeURIComponent(storeId);
 async function load(){const version=serial.current;try{const result=await api(path+'?'+scope);if(alive.current&&version===serial.current){setData(result);setError('')}}catch(e){if(alive.current&&version===serial.current)setError(e instanceof Error?e.message:'Не удалось обновить отправку')}}
 useEffect(()=>{alive.current=true;if(canManage)void load();const timer=setInterval(()=>{if(canManage&&(current.current.open||active(current.current.data?.status)))void load()},3000);return()=>{alive.current=false;clearInterval(timer)}},[storeId,canManage]);
 async function preview(page:number){if(!data)return;setPreviewBusy(true);try{const result=await api(path+'/'+encodeURIComponent(data.id)+'/items?'+scope+'&offset='+page);if(alive.current){setItems(result.items);setOffset(page)}}catch(e){if(alive.current)setError(e instanceof Error?e.message:'Не удалось загрузить тексты')}finally{if(alive.current)setPreviewBusy(false)}}
 useEffect(()=>{setAck(false);setItems([]);setOffset(0);if(open&&data?.status==='pending')void preview(0)},[open,data?.id,data?.status]);
 async function action(operation:string){serial.current++;setWorking(true);setError('');try{const result=await api(operation==='prepare'?path:path+'/'+encodeURIComponent(data!.id)+'/'+operation,'POST',{store_id:storeId});if(alive.current){setData(result);setAck(false)}if(operation!=='prepare')void onChange()}catch(e){if(alive.current)setError(e instanceof Error?e.message:'Не удалось выполнить действие')}finally{if(alive.current)setWorking(false)}}
 const pending=data?.status==='pending',sending=data?.status==='sending',preparing=data?.status==='preparing';
 return <>
  <button className="primary" disabled={busy||working||!canManage} onClick={()=>{setOpen(true);if(!data)void action('prepare')}}>{sending?'Отправка в WB: '+number(data?.published)+' / '+number(data?.total):preparing?'Собираем ответы для WB…':'Выгрузить все готовые ответы в WB'}</button>
  {open&&<ApprovalDialog busy={working} error={error} onClose={()=>setOpen(false)}>
   <h3>Все готовые ответы → Wildberries</h3>
   <p className="muted">Ответы выбранного магазина со всех страниц. Список фиксируется перед подтверждением; новые черновики автоматически в него не добавляются.</p>
   {working&&!data&&<p role="status">Создаём пакет…</p>}
   {data&&<div className="button-row"><button aria-pressed={excludedOpen} onClick={()=>setExcludedOpen(!excludedOpen)}>{excludedOpen?'К готовым ответам':'Показать исключённые ('+number(data.excluded)+')'}</button></div>}
   {data&&!active(data.status)&&<button disabled={working} onClick={()=>{setExcludedOpen(false);void action('prepare')}}>Собрать новый список ответов</button>}
   {data&&excludedOpen&&<BulkExcluded key={data.id} parentId={data.id} storeId={storeId} onReview={id=>{setOpen(false);onReview(id)}}/>}
   {data&&!excludedOpen&&<>
    <div className="metrics"><div className="metric"><span>Готовы к отправке</span><strong>{number(data.total)}</strong></div><div className="metric"><span>Исключены</span><strong>{number(data.excluded)}</strong></div><div className="metric"><span>Опубликованы</span><strong>{number(data.published)}</strong></div></div>
    {preparing&&<p role="status">Проверяем черновики со всех страниц: просмотрено {number(data.scanned)}. Можно закрыть окно — подготовка продолжится на сервере.</p>}
    <p className="muted">Низкие оценки, опасные ситуации, ответы без актуальной проверки правил и отзывы с уже начатой публикацией остаются для отдельной проверки.</p>
    {data.blocked&&<p className="error">Реальная отправка заблокирована настройками безопасности или тестовым режимом. Подготовка списка ничего не публикует.</p>}
    {data.job_error&&<p className="error" role="alert">Подготовка или отправка остановлена: {data.job_error}</p>}
    {pending&&<>
     <h4>Тексты ответов — {offset+1}–{Math.min(offset+20,data.total)} из {number(data.total)}</h4>
     {previewBusy&&<p role="status">Загружаем тексты…</p>}
     {items.map(item=><div className="action-preview" key={item.review_id}><small>Отзыв {item.review_id} · {item.rating}★ · версия {item.revision}</small><p style={{whiteSpace:'pre-wrap'}}>{item.text}</p></div>)}
     <div className="button-row"><button disabled={previewBusy||offset===0} onClick={()=>void preview(Math.max(0,offset-20))}>Предыдущие 20</button><button disabled={previewBusy||offset+20>=data.total} onClick={()=>void preview(offset+20)}>Следующие 20</button></div>
     <label className="check"><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>Я проверил ответы и подтверждаю реальную отправку всех {number(data.total)} ответов в Wildberries.</label>
     <p className="muted">Перед каждым запросом сервер повторно проверит текст, версию и правила. Изменившиеся ответы будут пропущены. Запросы с неопределённым результатом автоматически не повторяются.</p>
     <button className="primary" disabled={working||!ack||data.blocked||!items.length||previewBusy} onClick={()=>void action('confirm')}>Опубликовать {number(data.total)} ответов в WB</button>
    </>}
    {sending&&<><progress aria-label="Отправка ответов в Wildberries" max={Math.max(1,data.total)} value={data.processed}/><p role="status">Обработано {number(data.processed)} из {number(data.total)} · опубликовано {number(data.published)} · пропущено {number(data.skipped)} · нужна сверка {number(data.uncertain)}.</p><p className="muted">Отправка продолжится на сервере после закрытия сайта.</p></>}
    {['completed','needs_review','cancelled','empty'].includes(data.status)&&<p role="status">{data.status==='completed'?'Отправка завершена.':data.status==='empty'?'Проверенных ответов для массовой отправки сейчас нет.':data.status==='cancelled'?'Пакет остановлен.':'Пакет завершён; часть ответов требует отдельной проверки.'} Опубликовано: {number(data.published)}. Пропущено: {number(data.skipped)}. Нужна сверка: {number(data.uncertain)}.</p>}
    {data.message&&<p>{data.message}</p>}
    {active(data.status)&&<button disabled={working} onClick={()=>void action('cancel')}>{sending?'Остановить дальнейшую отправку':'Отменить пакет'}</button>}
   </>}
  </ApprovalDialog>}
 </>;
}
