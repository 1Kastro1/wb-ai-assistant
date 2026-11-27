'use client';

import {useEffect,useState} from 'react';
import {api} from './client';

const markets: Record<string,string> = {wb:'Wildberries',ozon:'Ozon',yandex:'Яндекс Маркет'};
const pageSize = 20;
type Template = {id:string; text:string; marketplaces:string[]; contexts:number; verified_at:string};

export default function ReplyTemplates(){
 const [input,setInput]=useState(''),[search,setSearch]=useState(''),[market,setMarket]=useState('');
 const [offset,setOffset]=useState(0),[result,setResult]=useState<{items:Template[];total:number}|null>(null);
 const [error,setError]=useState(''),[loading,setLoading]=useState(true),[feedback,setFeedback]=useState('');
 const [reload,setReload]=useState(0);
 useEffect(()=>{
  let active=true;
  setLoading(true);setError('');setFeedback('');
  const params=new URLSearchParams({q:search,marketplace:market,offset:String(offset),limit:String(pageSize)});
  api('/review-library/templates?'+params).then(data=>{if(active)setResult(data)})
   .catch(()=>{if(active)setError('Не удалось загрузить шаблоны.')})
   .finally(()=>{if(active)setLoading(false)});
  return()=>{active=false};
 },[market,search,offset,reload]);
 const total=result?.total??0;
 return <>
  <form className="card library-template-filters" onSubmit={event=>{event.preventDefault();setSearch(input.trim());setOffset(0);setReload(value=>value+1)}}>
   <label>Текст ответа<input type="search" value={input} maxLength={120} onChange={event=>setInput(event.target.value)} placeholder="Найти фразу в шаблоне"/></label>
   <label>Маркетплейс<select value={market} onChange={event=>{setMarket(event.target.value);setOffset(0)}}><option value="">Все площадки</option>{Object.entries(markets).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
   <button type="submit">Найти</button>
  </form>
  <p role="status" className="muted">{loading?'Загружаем сохранённые шаблоны…':feedback||(!error?`Найдено шаблонов: ${total.toLocaleString('ru-RU')}`:'')}</p>
  {error&&<p role="alert">{error} <button onClick={()=>setReload(value=>value+1)}>Повторить</button></p>}
  {!loading&&!error&&total===0&&<div className="empty">Подходящих шаблонов пока нет.</div>}
  {!loading&&!error&&result?.items.map(item=><article className="card library-case" key={item.id}>
   <div className="card-heading"><small>{item.marketplaces.map(id=>markets[id]||id).join(' · ')}</small><span className="pill">Контекстов: {item.contexts}</span></div>
   <p className="library-answer">{item.text}</p>
   <div className="card-heading"><small className="muted">Последняя проверка {new Date(item.verified_at).toLocaleDateString('ru-RU')}</small><button type="button" onClick={async()=>{try{await navigator.clipboard.writeText(item.text);setFeedback('Шаблон скопирован.')}catch{setFeedback('Не удалось скопировать. Текст можно выделить вручную.')}}}>Копировать</button></div>
  </article>)}
  {!loading&&!error&&total>pageSize&&<div className="button-row"><button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-pageSize))}>Назад</button><span>{offset+1}–{Math.min(offset+pageSize,total)} из {total.toLocaleString('ru-RU')}</span><button disabled={offset+pageSize>=total} onClick={()=>setOffset(offset+pageSize)}>Далее</button></div>}
 </>;
}
