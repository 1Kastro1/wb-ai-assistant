'use client';
import {useEffect,useState} from 'react';
import {api} from './client';
import FitmentConfirmation from './FitmentConfirmation';
import {vehicleIdentifiers} from './vehicleIdentifiers';
type Row=Record<string,any>;
export default function InternetFitment({question,initial,canManage,onChooseOEM,onSaved}:{question:Row;initial?:Row;canManage:boolean;onChooseOEM?:(item:Row)=>void;onSaved?:()=>void}){
 const [open,setOpen]=useState(false),[result,setResult]=useState<Row>(initial||{}),[enabled,setEnabled]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
 const vins=vehicleIdentifiers(question.text||'');
 useEffect(()=>{setResult(initial||{})},[initial,question.id]);
 useEffect(()=>{let cancelled=false;if(open)Promise.all([api('/fitment/internet/settings'),api('/fitment/questions/'+encodeURIComponent(question.id)+'/research')]).then(([settings,last])=>{if(!cancelled){setEnabled(settings.enabled);setResult(last)}}).catch(()=>{if(!cancelled)setError('Не удалось загрузить историю подбора. Повторите открытие раздела.')});return()=>{cancelled=true}},[open,question.id]);
 async function refresh(){setResult(await api('/fitment/questions/'+encodeURIComponent(question.id)+'/research'))}
 async function search(){setBusy(true);setError('');try{await api('/fitment/questions/'+encodeURIComponent(question.id)+'/research','POST',{refresh:true},25000);await refresh()}catch{setError('Поиск сейчас не завершён. Можно открыть каталог в браузере и сохранить проверенный результат ниже.')}finally{setBusy(false)}}
 const confirmed=['FIT','ALTERNATIVE'].includes(result.saved_fitment?.status);
 const label=confirmed?'подбор подтверждён':({FOUND:'найден OEM',ACCESS_REQUIRED:'доступ ограничен',NOT_FOUND:'нет данных',UNAVAILABLE:'каталог недоступен',MULTIPLE_VEHICLES:'нужна модификация',NEEDS_REVIEW:'нужна проверка',UNSUPPORTED:'нужна проверка категории',NEEDS_VIN:'нужен VIN или Frame'} as Row)[result.status]||'';
 return <details open={open} onToggle={e=>setOpen(e.currentTarget.open)} className="fitment-internet"><summary>Подбор в интернет-каталогах{label?' · '+label:''}</summary>
 <p>Поиск проверяет оригинальный OEM по VIN или Frame, затем сопоставляет его с подтверждёнными аналогами Вашего магазина. Расшифровка номера автомобиля сама по себе не подтверждает деталь.</p>
 {canManage&&<button disabled={busy||!enabled||vins.length!==1} onClick={search} aria-busy={busy}>{busy?'Проверяем каталоги — до 18 секунд…':'Проверить VIN / Frame в интернете'}</button>}
 {!enabled&&<p className="muted">Владелец может включить интернет-подбор в разделе «Товары».</p>}{vins.length!==1&&<p className="muted">Для поиска нужен один полный VIN или Frame в вопросе покупателя.</p>}
 {error&&<p role="status">{error}</p>}{confirmed?<p role="status"><b>{result.saved_fitment.text}</b> · подтверждение сохранено в каталоге магазина</p>:result.message&&<p role="status"><b>{result.message}</b></p>}{result.checked_at&&<small>Интернет-каталог проверен: {new Date(result.checked_at).toLocaleString('ru-RU')}{result.cached?' · сохранённая попытка':''}</small>}
 {result.vehicle?.make&&<p>Автомобиль: {[result.vehicle.make,result.vehicle.model,result.vehicle.year,result.vehicle.engine_code,result.vehicle.body].filter(Boolean).join(' · ')}. {!result.vehicle.complete&&'Расшифровка неполная — требуется уточнение.'}</p>}
 {result.vehicles?.length>1&&<ul>{result.vehicles.map((v:Row,i:number)=><li key={i}>{v.make} {v.name}</li>)}</ul>}
 {result.oem_candidates?.length>0&&<><h4>Оригинальные номера из каталога</h4>{result.oem_candidates.map((p:Row,i:number)=><p key={i}><b>{p.oe_brand} {p.oe_number}</b> · {p.description} · <a href={p.source} target="_blank" rel="noopener noreferrer">Источник ↗</a>{result.status!=='FOUND'&&' · требуется проверка'}{onChooseOEM&&<button onClick={()=>onChooseOEM(p)}>Перенести OEM в форму проверки</button>}</p>)}</>}
 {result.our_candidates?.length>0&&<><h4>Наши аналоги для проверки</h4>{result.our_candidates.map((p:Row)=><p key={p.wb_nm_id}>{p.brand} {p.part_number} · <a href={'https://www.wildberries.ru/catalog/'+p.wb_nm_id+'/detail.aspx'} target="_blank" rel="noopener noreferrer">WB {p.wb_nm_id} ↗</a></p>)}</>}
 {result.status==='FOUND'&&<p>Результат сохранён. Нажмите «Подобрать лучший ответ», чтобы использовать актуальные сведения в черновике.</p>}
 {vins.length===1&&<p><a href={'https://partsouq.com/en/search/all?q='+encodeURIComponent(vins[0])} target="_blank" rel="noopener noreferrer">Открыть VIN / Frame в PartSouq ↗</a> · <a href={'https://uniqom.ru/original-catalogs?identString='+encodeURIComponent(vins[0])} target="_blank" rel="noopener noreferrer">Открыть UNIQOM ↗</a></p>}
 <small>UNIQOM доступен через Ваш вход в браузере. Сервер не использует эту авторизацию. Если каталог требует входа или выбора модификации, сохраните проверенный подбор ниже. Отсутствие результата поиска не означает отсутствие товара.</small>
 {canManage&&result.can_confirm&&result.product&&vins.length===1&&<FitmentConfirmation question={question} product={result.product} onSaved={()=>{refresh().catch(()=>setError('Подбор сохранён, но историю не удалось обновить. Откройте раздел повторно.'));onSaved?.()}}/>}
 </details>;
}
