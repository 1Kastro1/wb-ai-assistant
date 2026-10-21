'use client';
import {useEffect,useState} from 'react';
import {vehicleIdentifiers} from './vehicleIdentifiers';
import {uniqomVehicleUrl,uniqomOemUrl} from './uniqomCatalog';
import './uniqom-fitment.css';
type Row=Record<string,any>;
export default function UniqomFitment({question,product,coverage={},confirmed=false}:{question:Row;product?:Row;coverage?:Row;confirmed?:boolean}){
 const ids=vehicleIdentifiers(question.text||''),[group,setGroup]=useState(coverage.group_id||''),[message,setMessage]=useState('');
 useEffect(()=>{setGroup(coverage.group_id||'');setMessage('')},[question.id,coverage.group_id]);
 const systems:Row[]=coverage.systems||[],selected=systems.find(item=>item.id===group);
 async function copy(){try{await navigator.clipboard.writeText(ids[0]);setMessage('Номер скопирован')}catch{setMessage('Скопируйте номер из вопроса покупателя вручную')}}
 return <section className="uniqom-fitment" aria-label="Подбор через Uniqom">
  <div className="uniqom-fitment-heading"><div><small>ОРИГИНАЛЬНЫЕ КАТАЛОГИ</small><h4>Подбор через Uniqom</h4></div><span className="pill">{confirmed?'Подтверждено':'Все категории'}</span></div>
  <p>{product?.category||coverage.category||'Нужная категория выбирается в каталоге автомобиля.'}{product?.part_number?' · '+product.brand+' '+product.part_number:''}</p>
  <div className="uniqom-fitment-actions"><a className="primary" href={uniqomVehicleUrl(question.text)} target="_blank" rel="noopener noreferrer">{ids.length===1?'Открыть автомобиль в Uniqom':'Открыть каталоги Uniqom'} ↗</a>{ids.length===1&&<button type="button" onClick={()=>void copy()}>Скопировать {ids[0].length===17?'VIN':'Frame'}</button>}</div>
  {ids.length>1&&<p className="orange">В вопросе несколько номеров автомобиля. Выберите нужный номер в каталоге.</p>}
  {message&&<small role="status">{message}</small>}
  {systems.length>0&&<label>Система автомобиля<select value={group} onChange={e=>setGroup(e.target.value)}>{systems.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select></label>}
  <ol className="uniqom-fitment-steps"><li>Проверьте модель, дату выпуска и модификацию найденного автомобиля.</li><li>Откройте {selected?'«'+selected.label+'»':'нужный узел'}, затем выберите деталь. Для колодок проверьте переднюю или заднюю ось.</li><li>Откройте найденный OEM в поиске Uniqom и проверьте точный бренд и артикул аналога.</li><li>Сохраните проверенный подбор в форме ниже. Ответ будет использовать подтверждённые сведения.</li></ol>
  {question.oe_number&&<a href={uniqomOemUrl(question.oe_number)} target="_blank" rel="noopener noreferrer">Найти аналоги OEM {question.oe_number} ↗</a>}
  <small>Каталог открывается в отдельной вкладке через Ваш вход в Uniqom. Для любой категории можно выбрать узел вручную; совместимость подтверждается после проверки OEM и аналога.</small>
 </section>;
}

