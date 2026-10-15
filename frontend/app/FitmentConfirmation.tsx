'use client';
import {useEffect,useState} from 'react';
import {api} from './client';
type Row=Record<string,any>;
export default function FitmentConfirmation({question,product,onSaved}:{question:Row;product:Row;onSaved:()=>void}){
 const [form,setForm]=useState<Row>({}),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 useEffect(()=>{setForm({brand:product.brand||'',part_number:product.part_number||'',wb_nm_id:product.id||question.product_id,source:'https://uniqom.ru/original-catalogs',fit:true,oe_brand:'',oe_number:'',remember_cross:false});setError('');setMessage('')},[question.id,product.id,product.brand,product.part_number,question.product_id]);
 const field=(key:string,value:any)=>setForm(v=>({...v,[key]:value}));
 async function save(){setBusy(true);setError('');setMessage('');try{await api('/fitment/questions/'+encodeURIComponent(question.id)+'/evidence','POST',form);setMessage('Подбор сохранён. Сведения доступны для подготовки ответа.');onSaved()}catch(e){setError(e instanceof Error?e.message:'Не удалось сохранить подбор')}finally{setBusy(false)}}
 return <details className="fitment-confirmation"><summary>Сохранить проверенный подбор из UNIQOM</summary><p>После проверки каталога укажите найденную деталь. Оригинальный OEM связывает автомобиль с аналогами разных брендов; он отличается от артикула продаваемой детали.</p>
 <div className="two-columns"><label>Марка оригинальной детали<input value={form.oe_brand||''} onChange={e=>field('oe_brand',e.target.value)} placeholder="NISSAN"/></label><label>Оригинальный OEM-номер<input value={form.oe_number||''} onChange={e=>field('oe_number',e.target.value)} placeholder="15208-65F0A"/></label></div>
 <div className="two-columns"><label>Бренд подходящего товара<input value={form.brand||''} onChange={e=>field('brand',e.target.value)}/></label><label>Артикул производителя<input value={form.part_number||''} onChange={e=>field('part_number',e.target.value)} placeholder="DF0003"/></label></div>
 <label>Артикул нашего товара WB<input value={form.wb_nm_id||''} onChange={e=>field('wb_nm_id',e.target.value)}/></label><label>Ссылка на проверенный источник<input value={form.source||''} onChange={e=>field('source',e.target.value)}/></label>
 <label className="check"><input type="checkbox" checked={!!form.remember_cross} onChange={e=>field('remember_cross',e.target.checked)}/>Подтверждаю OEM по этому VIN и взаимозаменяемость указанного товара — использовать кросс в следующих подборах</label><small>Для другого VIN сначала потребуется подтверждение его оригинального OEM. Ограничения по двигателю, оси и стороне необходимо проверить перед сохранением.</small>
 {error&&<p role="alert" className="save-error">{error}</p>}{message&&<p role="status">{message}</p>}
 <button className="primary" disabled={busy||!form.brand?.trim()||!form.part_number?.trim()||!form.wb_nm_id?.trim()||(form.remember_cross&&(!form.oe_brand?.trim()||!form.oe_number?.trim()))} onClick={save}>{busy?'Сохраняем…':'Проверил каталог — сохранить подбор'}</button></details>;
}
