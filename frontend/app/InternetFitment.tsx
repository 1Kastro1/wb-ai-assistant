'use client';
import {useEffect,useState} from 'react';
import {api} from './client';
import ProductThumbnail from './ProductThumbnail';
import FitmentConfirmation from './FitmentConfirmation';
import UniqomFitment from './UniqomFitment';
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
 const label=confirmed?'подбор подтверждён':({FOUND:'найден OEM',ACCESS_REQUIRED:'PartSouq ограничил доступ',NOT_FOUND:'нет данных',UNAVAILABLE:'PartSouq недоступен',MULTIPLE_VEHICLES:'нужна модификация',NEEDS_REVIEW:'нужна проверка',CATALOG_SELECTION:'выбор узла в Uniqom',UNSUPPORTED:'выбор узла в Uniqom',NEEDS_VIN:'нужен VIN или Frame'} as Row)[result.status]||'';
 return <details open={open} onToggle={e=>setOpen(e.currentTarget.open)} className="fitment-internet"><summary>Подбор в интернет-каталогах{label?' · '+label:''}</summary>
 <UniqomFitment question={question} product={result.product||question.product} coverage={result.coverage} confirmed={confirmed}/>
 <details className="fitment-public-check"><summary>Дополнительная автоматическая проверка в PartSouq</summary><p>Проверяет оригинальный OEM по VIN или Frame и подтверждённые аналоги магазина. Для остальных категорий выберите узел в Uniqom.</p>
 {canManage&&result.coverage?.automatic_supported!==false&&<button disabled={busy||!enabled||vins.length!==1} onClick={search} aria-busy={busy}>{busy?'Проверяем каталоги — до 18 секунд…':'Проверить VIN / Frame в интернете'}</button>}
 {result.coverage?.automatic_supported===false&&<p>Для этой категории выберите нужный узел в Uniqom и сохраните результат ниже.</p>}
 {!enabled&&<p className="muted">Владелец может включить интернет-подбор для всех маркетплейсов в разделе «Товары».</p>}{vins.length!==1&&<p className="muted">Для поиска нужен один полный VIN или Frame в вопросе покупателя.</p>}
 {error&&<p role="status">{error}</p>}{confirmed?<p role="status"><b>{result.saved_fitment.text}</b> · подтверждение сохранено в каталоге магазина</p>:result.message&&<p role="status"><b>{result.message}</b></p>}{result.checked_at&&<small>{result.status==='CATALOG_SELECTION'?'Путь подбора подготовлен':'Проверка PartSouq'}: {new Date(result.checked_at).toLocaleString('ru-RU')}{result.cached?' · сохранённая попытка':''}</small>}
 {result.vehicle?.make&&<p>Автомобиль: {[result.vehicle.make,result.vehicle.model,result.vehicle.year,result.vehicle.engine_code,result.vehicle.body].filter(Boolean).join(' · ')}. {!result.vehicle.complete&&'Расшифровка неполная — требуется уточнение.'}</p>}
 {result.vehicles?.length>1&&<ul>{result.vehicles.map((v:Row,i:number)=><li key={i}>{v.make} {v.name}</li>)}</ul>}
 {result.oem_candidates?.length>0&&<><h4>Оригинальные номера из каталога</h4>{result.oem_candidates.map((p:Row,i:number)=><p key={i}><b>{p.oe_brand} {p.oe_number}</b> · {p.description} · <a href={p.source} target="_blank" rel="noopener noreferrer">Источник ↗</a>{result.status!=='FOUND'&&' · требуется проверка'}{onChooseOEM&&<button onClick={()=>onChooseOEM(p)}>Перенести OEM в форму проверки</button>}</p>)}</>}
 {result.our_candidates?.length>0&&<><h4>Наши аналоги для проверки</h4>{result.our_candidates.map((p:Row)=><div className="catalog-candidate" key={p.product_id||p.wb_nm_id}><ProductThumbnail product={{...p,name:p.name||[p.brand,p.part_number].join(" ")}}/><div className="candidate-copy"><b>{p.brand} {p.part_number}</b><small>{p.name||"Товар магазина · совместимость требует подтверждения"}</small>{p.url?<a href={p.url} target="_blank" rel="noopener noreferrer">{p.article_label||'Открыть товар'} ↗</a>:<small>{p.article_label||p.product_id||p.wb_nm_id}</small>}</div></div>)}</>}
 {result.status==='FOUND'&&<p>Результат сохранён. Нажмите «Новый вариант», чтобы использовать актуальные сведения в черновике.</p>}
 {vins.length===1&&<p><a href={'https://partsouq.com/en/search/all?q='+encodeURIComponent(vins[0])} target="_blank" rel="noopener noreferrer">Открыть VIN / Frame в PartSouq ↗</a></p>}</details>
 <small>Отсутствие результата автоматического поиска не означает отсутствие товара. Подтвердите OEM и аналог в Uniqom, затем сохраните результат ниже.</small>
 {canManage&&result.can_confirm&&result.product&&vins.length===1&&<FitmentConfirmation question={question} product={result.product} onSaved={()=>{refresh().catch(()=>setError('Подбор сохранён, но историю не удалось обновить. Откройте раздел повторно.'));onSaved?.()}}/>}
 </details>;
}

