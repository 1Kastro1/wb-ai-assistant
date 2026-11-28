'use client';

import {useEffect,useState} from 'react';
import {api} from './client';

const markets: Record<string,string> = {wb:'Wildberries',ozon:'Ozon',yandex:'Яндекс Маркет'};
const pageSize = 20;
type Template = {
 id:string; text:string; marketplaces:string[]; verified_at:string;
 revision:number; categories:string[]; products:string[]; ratings:number[]; brands?:string[];
};
type TemplatePage = {items:Template[];total:number;filters?:{brands:string[];categories:string[]}};

export default function ReplyTemplates(){
 const [input,setInput]=useState(''),[search,setSearch]=useState(''),[market,setMarket]=useState('');
 const [brand,setBrand]=useState(''),[category,setCategory]=useState('');
 const [offset,setOffset]=useState(0),[result,setResult]=useState<TemplatePage|null>(null);
 const [error,setError]=useState(''),[loading,setLoading]=useState(true),[feedback,setFeedback]=useState('');
 const [reload,setReload]=useState(0);
 const [editor,setEditor]=useState<{id:string;text:string;revision:number}|null>(null);
 const [saving,setSaving]=useState(false),[saveError,setSaveError]=useState('');
 useEffect(()=>{
  let active=true;
  setLoading(true);setError('');
  const params=new URLSearchParams({q:search,marketplace:market,brand,category,offset:String(offset),limit:String(pageSize)});
  api('/review-library/templates?'+params).then(data=>{if(active)setResult(data)})
   .catch(()=>{if(active)setError('Не удалось загрузить шаблоны.')})
   .finally(()=>{if(active)setLoading(false)});
  return()=>{active=false};
 },[market,brand,category,search,offset,reload]);
 const total=result?.total??0;
 const filtered=Boolean(input||search||market||brand||category);
 async function saveTemplate(){
  if(!editor||!editor.text.trim()||saving)return;
  setSaving(true);setSaveError('');setFeedback('');
  try{
   await api('/review-library/templates/'+editor.id,'PATCH',{text:editor.text,revision:editor.revision});
   setEditor(null);setFeedback('Шаблон сохранён. При использовании ответ пройдёт проверку по правилам.');
   setOffset(0);setReload(value=>value+1);
  }catch(error){
   setSaveError(error instanceof Error?error.message:'Не удалось сохранить шаблон.');
  }finally{setSaving(false)}
 }
 return <section className="saved-templates" aria-label="Шаблоны ответов на отзывы">
  <div className="template-intro">
   <div>
    <h2>Шаблоны ответов</h2>
    <p>Готовые формулировки и ситуации, для которых они были сохранены.</p>
   </div>
   {!loading&&!error&&<span className="template-count">{total.toLocaleString('ru-RU')} в списке</span>}
  </div>
  <form className="card library-template-filters" onSubmit={event=>{event.preventDefault();setFeedback('');setSearch(input.trim());setOffset(0);setReload(value=>value+1)}}>
   <label>Текст ответа<input type="search" disabled={editor!==null} value={input} maxLength={120} onChange={event=>setInput(event.target.value)} placeholder="Найти фразу в шаблоне"/></label>
   <label>Маркетплейс<select disabled={editor!==null} value={market} onChange={event=>{setFeedback('');setMarket(event.target.value);setOffset(0)}}><option value="">Все площадки</option>{Object.entries(markets).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label>
   <label>Бренд<select disabled={editor!==null} value={brand} onChange={event=>{setFeedback('');setBrand(event.target.value);setOffset(0)}}><option value="">Все бренды</option>{result?.filters?.brands.map(value=><option key={value}>{value}</option>)}</select></label>
   <label>Категория отзыва<select disabled={editor!==null} value={category} onChange={event=>{setFeedback('');setCategory(event.target.value);setOffset(0)}}><option value="">Все категории</option>{result?.filters?.categories.map(value=><option key={value}>{value}</option>)}</select></label>
   <div className="template-filter-actions">
    <button type="submit" disabled={editor!==null}>Найти</button>
    {filtered&&<button type="button" disabled={editor!==null} onClick={()=>{setInput('');setSearch('');setMarket('');setBrand('');setCategory('');setOffset(0);setFeedback('')}}>Сбросить</button>}
   </div>
  </form>
  <p role="status" className="template-status">{loading?'Загружаем сохранённые шаблоны…':feedback}</p>
  {error&&<p role="alert">{error} <button onClick={()=>setReload(value=>value+1)}>Повторить</button></p>}
  {!loading&&!error&&total===0&&<div className="empty">Подходящих шаблонов пока нет.</div>}
  {!loading&&!error&&<div className="template-list">{result?.items.map(item=><article className="template-card" key={item.id}>
   <header className="template-card-head">
    <div className="template-categories" aria-label="Категории отзывов">
     {(item.categories.length?item.categories:['Категория неизвестна']).map(category=><span key={category}>{category}</span>)}
    </div>
    {item.ratings.length>0&&<span className="template-rating" aria-label={'Оценки: '+item.ratings.join(', ')}>
     {item.ratings.join(', ')} ★
    </span>}
   </header>
   <div className="template-meta">
    <span>{item.brands?.length?<><strong>{item.brands.length===1?'Бренд':'Бренды'}</strong> {item.brands.join(' · ')}</>:'Бренд не указан'}</span>
    <span>{item.marketplaces.map(id=>markets[id]||id).join(' · ')}</span>
    {item.products.length>0&&<details><summary>Категории товаров ({item.products.length})</summary>{item.products.join(' · ')}</details>}
   </div>
   {editor?.id===item.id?<form className="template-editor" onSubmit={event=>{event.preventDefault();void saveTemplate()}}>
    <label>Текст ответа<textarea autoFocus rows={5} maxLength={5000} value={editor.text} disabled={saving}
     onChange={event=>setEditor({...editor,text:event.target.value})}/></label>
    {saveError&&<p className="save-error" role="alert">{saveError}</p>}
    <div className="template-footer">
     <small className="muted">{editor.text.length.toLocaleString('ru-RU')} / 5 000</small>
     <div className="button-row">
      <button type="button" disabled={saving} onClick={()=>{setEditor(null);setSaveError('')}}>Отмена</button>
      <button className="primary" type="submit" disabled={saving||!editor.text.trim()||editor.text.trim()===item.text}>{saving?'Сохраняем…':'Сохранить'}</button>
     </div>
    </div>
   </form>:<>
    <p className="library-answer template-text">{item.text}</p>
    <div className="template-footer">
     <small className="muted">{item.revision>0?'Изменён владельцем · проверяется при использовании':`Последняя проверка ${new Date(item.verified_at).toLocaleDateString('ru-RU')}`}</small>
     <div className="button-row">
      <button type="button" onClick={async()=>{try{await navigator.clipboard.writeText(item.text);setFeedback('Шаблон скопирован.')}catch{setFeedback('Не удалось скопировать. Текст можно выделить вручную.')}}}>Копировать</button>
      <button className="template-edit-button" type="button" disabled={editor!==null}
       onClick={()=>{setEditor({id:item.id,text:item.text,revision:item.revision});setSaveError('')}}>Редактировать</button>
     </div>
    </div>
   </>}
  </article>)}</div>}
  {!loading&&!error&&total>pageSize&&<div className="button-row"><button disabled={editor!==null||!offset} onClick={()=>setOffset(Math.max(0,offset-pageSize))}>Назад</button><span>{offset+1}–{Math.min(offset+pageSize,total)} из {total.toLocaleString('ru-RU')}</span><button disabled={editor!==null||offset+pageSize>=total} onClick={()=>setOffset(offset+pageSize)}>Далее</button></div>}
 </section>;
}
