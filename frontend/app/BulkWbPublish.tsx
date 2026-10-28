'use client';
import {useEffect,useRef,useState} from 'react';
import {api} from './client';
import ApprovalDialog from './ApprovalDialog';
import './wb-publication.css';
import {publicationDestination} from './publication-scope.mjs';

type Row=Record<string,any>;
const active=(status:string)=>['preparing','pending','sending'].includes(status);
const number=(value:number=0)=>value.toLocaleString('ru-RU');

type Props={storeId:string;provider:string;canManage:boolean;busy:boolean;onChange:()=>Promise<void>;onReview:(id:string)=>void;onLowRatings:()=>void};
export default function BulkWbPublish(props:Props){
 const [mode,setMode]=useState<'all'|'positive'|null>(null);
 const [statuses,setStatuses]=useState<Record<string,Row|null>>({});
 const statusRef=useRef(statuses);statusRef.current=statuses;
 const destination=publicationDestination(props.provider);
 useEffect(()=>{let live=true;async function update(){try{const values=await Promise.all(['all','positive'].map(scope=>api('/publications/reviews/all?store_id='+encodeURIComponent(props.storeId)+'&rating_scope='+scope)));if(live)setStatuses({all:values[0],positive:values[1]})}catch{}}
 if(props.canManage)void update();const timer=setInterval(()=>{if(props.canManage&&Object.values(statusRef.current).some(value=>active(value?.status)))void update()},3000);return()=>{live=false;clearInterval(timer)}},[props.storeId,props.canManage,mode]);
 const label=(scope:string,fallback:string)=>statuses[scope]?.status==='preparing'?'Собираем ответы → '+destination.short:statuses[scope]?.status==='sending'?'Отправляем → '+destination.short:fallback;
 return <>
  <button className="primary" disabled={props.busy||!props.canManage} onClick={()=>setMode('positive')}>{label('positive','Выгрузить положительные → '+destination.short)}</button>
  <button disabled={props.busy||!props.canManage} onClick={props.onLowRatings}>Проверить низкие оценки</button>
  <button disabled={props.busy||!props.canManage} onClick={()=>setMode('all')}>{label('all','Все ответы → '+destination.short)}</button>
  {mode&&<BulkWbPublishMode key={props.storeId+mode} {...props} ratingScope={mode} onDismiss={()=>setMode(null)}/>}
 </>;
}

function BulkWbPublishMode({storeId,provider,canManage,onChange,onReview,ratingScope,onDismiss}:Props&{ratingScope:'all'|'positive';onDismiss:()=>void}){
 const destination=publicationDestination(provider);
 const [includeExcluded,setIncludeExcluded]=useState(true),[excludedAck,setExcludedAck]=useState(false);
 const [data,setData]=useState<Row|null>(null),[working,setWorking]=useState(false),[error,setError]=useState(''),[ack,setAck]=useState(false),[loading,setLoading]=useState(true);
 const open=true;
 const [items,setItems]=useState<Row[]>([]),[offset,setOffset]=useState(0),[previewBusy,setPreviewBusy]=useState(false);
 const alive=useRef(true),serial=useRef(0),current=useRef({data,open});current.current={data,open};
 const path='/publications/reviews/all',scope='store_id='+encodeURIComponent(storeId)+'&rating_scope='+ratingScope;
 async function load(){const version=serial.current;try{const result=await api(path+'?'+scope);if(alive.current&&version===serial.current){setData(result);setError('')}}catch(e){if(alive.current&&version===serial.current)setError(e instanceof Error?e.message:'Не удалось обновить отправку')}}
 useEffect(()=>{serial.current++;setData(null);setWorking(false);setPreviewBusy(false);setAck(false);setExcludedAck(false);setError('')},[storeId]);
 useEffect(()=>{alive.current=true;if(canManage)void load().finally(()=>{if(alive.current)setLoading(false)});const timer=setInterval(()=>{if(canManage&&(current.current.open||active(current.current.data?.status)))void load()},3000);return()=>{alive.current=false;clearInterval(timer)}},[storeId,canManage]);
 async function preview(page:number){if(!data)return;const version=serial.current;setPreviewBusy(true);try{const result=await api(path+'/'+encodeURIComponent(data.id)+'/items?'+scope+'&offset='+page);if(alive.current&&version===serial.current){setItems(result.items);setOffset(page)}}catch(e){if(alive.current&&version===serial.current)setError(e instanceof Error?e.message:'Не удалось загрузить тексты')}finally{if(alive.current&&version===serial.current)setPreviewBusy(false)}}
 useEffect(()=>{setAck(false);setExcludedAck(false);setItems([]);setOffset(0);if(open&&data?.status==='pending')void preview(0)},[open,data?.id,data?.status]);
 async function action(operation:string){const version=++serial.current;setWorking(true);setError('');try{const result=await api(operation==='prepare'?path:path+'/'+encodeURIComponent(data!.id)+'/'+operation,'POST',{store_id:storeId,include_excluded:includeExcluded,excluded_ack:excludedAck,rating_scope:ratingScope});if(alive.current&&version===serial.current){setData(result);setAck(false);setExcludedAck(false)}if(operation!=='prepare')void onChange()}catch(e){if(alive.current&&version===serial.current)setError(e instanceof Error?e.message:'Не удалось выполнить действие')}finally{if(alive.current&&version===serial.current)setWorking(false)}}
 const pending=data?.status==='pending',sending=data?.status==='sending',preparing=data?.status==='preparing';
 return <>
  {open&&<ApprovalDialog title={(ratingScope==='positive'?'Положительные отзывы → ':'Выгрузка → ')+destination.name} description="Сохранённые ответы со всех страниц магазина. Отправка — после вашего подтверждения." busy={working} error={error} onClose={onDismiss}>
   <div className="wb-export" data-provider={provider}>
   <div className="wb-export-destination"><span className="wb-export-logo">{destination.logo}</span><div><strong>Ответы покупателям · {destination.name}</strong><span>Без ожидания перегенерации</span></div></div>
   <div className="wb-export-notice">{ratingScope==='positive'?'Только 4–5 звёзд без выявленных жалоб и опасных ситуаций. ':''}Отзывы с оценкой 1–3 звезды отправляются отдельно, после вашей личной проверки. Тексты ответов сохраняются.</div>
   {loading&&<p role="status">Проверяем текущий список…</p>}
   {!loading&&(!data||!active(data.status))&&<>
    <fieldset className="wb-export-options"><legend>Какие ответы выгрузить</legend>
     <label className={includeExcluded?'selected':''}><input type="radio" name="wb-export-mode" checked={includeExcluded} onChange={()=>setIncludeExcluded(true)}/><span><strong>Сохранённые, включая ранее исключённые</strong><small>В том числе без завершённой автопроверки. Подтверждает владелец. Оценки 1–3 сюда не входят.</small></span></label>
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
     <div className="wb-export-previews">{items.map(item=><article className="wb-export-reply" key={item.review_id}><header><span>{item.rating} ★</span>{item.owner_review&&<small>На решение владельца</small>}<button onClick={()=>{onDismiss();onReview(item.review_id)}}>Открыть отзыв →</button></header>{item.review_text&&<blockquote>{item.review_text}</blockquote>}<p>{item.text}</p></article>)}</div>
     <div className="button-row"><button disabled={previewBusy||offset===0} onClick={()=>void preview(Math.max(0,offset-20))}>Предыдущие 20</button><button disabled={previewBusy||offset+20>=data.total} onClick={()=>void preview(offset+20)}>Следующие 20</button></div>
     <label className="check"><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>Я проверил ответы и подтверждаю реальную отправку всех {number(data.total)} ответов в {destination.name}.</label>
     {data.include_excluded&&<label className="check"><input type="checkbox" checked={excludedAck} onChange={e=>setExcludedAck(e.target.checked)}/>Как владелец подтверждаю тексты без автоматического одобрения. Низкие оценки проверяю отдельно.</label>}
     <button className="primary wb-export-main" disabled={working||!ack||(data.include_excluded&&!excludedAck)||data.blocked||!items.length||previewBusy} onClick={()=>void action('confirm')}>Отправить {number(data.total)} ответов → {destination.short}</button>
    </>}
    {sending&&<><progress aria-label={'Отправка ответов в '+destination.name} max={Math.max(1,data.total)} value={data.processed}/><p role="status">Обработано {number(data.processed)} из {number(data.total)} · опубликовано {number(data.published)} · пропущено {number(data.skipped)} · нужна сверка {number(data.uncertain)}.</p><p className="muted">Отправка продолжится на сервере после закрытия сайта.</p></>}
    {['completed','needs_review'].includes(data.status)&&<p role="status">{data.status==='completed'?'Отправка завершена.':data.status==='empty'?'Проверенных ответов для массовой отправки сейчас нет.':data.status==='cancelled'?'Пакет остановлен.':'Пакет завершён; часть ответов требует отдельной проверки.'} Опубликовано: {number(data.published)}. Пропущено: {number(data.skipped)}. Нужна сверка: {number(data.uncertain)}.</p>}
    {active(data.status)&&<button disabled={working} onClick={()=>void action('cancel')}>{sending?'Остановить дальнейшую отправку':'Отменить пакет'}</button>}
   </>}
   </div>
  </ApprovalDialog>}
 </>;
}

