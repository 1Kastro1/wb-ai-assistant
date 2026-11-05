'use client';
import {useEffect,useId,useRef,useState,CSSProperties} from 'react';
import {api} from './client';
import {topicLabels} from './operations.mjs';
import ProblemProducts from './ProblemProducts';
import './product-feedback.css';

type Product={id:string;name:string;brand?:string;negative:number;average:number};
type Stats={product_id:string;period_days:number;total:number;unanswered:number;negative:number;average:number;rating_distribution:Record<string,number>;negative_topics:Record<string,number>};
type Row=Record<string,any>;
const number=(value:number)=>value.toLocaleString('ru-RU');
const period=(days:number)=>days?`За последние ${days} дней`:'За всё время';

export function ProductFeedbackCard({product,data,days,loading,error,onRetry,onClear,onReviews}:{product:Product|null;data:Stats|null;days:number;loading:boolean;error:string;onRetry:()=>void;onClear:()=>void;onReviews:(negative:boolean)=>void}){
 const title=useId();
 // A new selection must never display the previous product's or period's totals.
 const current=product&&data?.product_id===product.id&&data.period_days===days?data:null;
 const share=current?.total?Math.round(current.negative/current.total*1000)/10:0;
 const topics=Object.entries(current?.negative_topics||{}).sort((a,b)=>b[1]-a[1]).slice(0,5);
 return <section id="product-feedback-panel" className={'card product-feedback-card'+(product?' has-selection':'')} aria-labelledby={title} aria-busy={loading}>
  <div className="product-feedback-heading"><div><span className="feedback-eyebrow">ОБРАТНАЯ СВЯЗЬ</span><h3 id={title} tabIndex={-1}>{product?'Статистика товара':'Выберите товар'}</h3></div>{product&&<button className="feedback-clear" onClick={onClear} aria-label="Сбросить выбранный товар">Сбросить ×</button>}</div>
  {!product?<div className="feedback-placeholder"><span className="feedback-placeholder-icon" aria-hidden="true">▥</span><h4>Всё о впечатлениях покупателей</h4><p>Нажмите на товар в списке жалоб ниже — здесь появятся его оценки, темы обратной связи и доля отзывов с жалобами.</p><small>Статистика выбранного магазина · {period(days).toLowerCase()}</small></div>:<>
   <div className="feedback-product"><span className="feedback-brand">{product.brand||'Без бренда'}</span><h4>{product.name}</h4><p>Артикул: {product.id} <span aria-hidden="true">·</span> {period(days)}</p></div>
   {loading&&!current?<div className="feedback-loading" role="status"><span className="feedback-loading-mark" aria-hidden="true"/><b>Загружаем обратную связь по товару…</b><p>Собираем оценки и темы отзывов</p></div>:error?<div className="feedback-error" role="alert"><b>Не удалось загрузить статистику</b><p>{error}</p><button onClick={onRetry}>Повторить загрузку</button></div>:current?<>
    <div className="feedback-summary"><div className="feedback-score" style={{'--feedback-score':`${current.average/5*100}%`} as CSSProperties} aria-label={current.total?`Средняя оценка ${current.average} из 5`:'Оценок пока нет'}><div><strong>{current.total?current.average.toLocaleString('ru-RU',{maximumFractionDigits:2}):'—'}</strong><span>из 5 звёзд</span></div></div><div className="feedback-summary-copy"><b>{number(current.total)} отзывов</b><p>Средняя оценка по всем отзывам о выбранном товаре</p><span className="feedback-complaint-share">{number(share)}% отзывов с оценкой 1–3★</span></div></div>
    <div className="feedback-metrics"><div><strong>{number(current.total)}</strong><span>Всего отзывов</span></div><div className="feedback-negative"><strong>{number(current.negative)}</strong><span>С оценкой 1–3★</span></div><div><strong>{number(current.unanswered)}</strong><span>Без ответа</span></div></div>
    <div className="feedback-ratings"><h4>Распределение оценок</h4>{[5,4,3,2,1].map(star=>{const count=Number(current.rating_distribution?.[star]||0);return <div className={'feedback-rating-row'+(star<=3?' low-rating':'')} key={star} aria-label={`${star} звёзд: ${count}`}><span>{star} <i aria-hidden="true">★</i></span><div className="feedback-track" aria-hidden="true"><span style={{width:`${current.total?count/current.total*100:0}%`}}/></div><b>{number(count)}</b></div>})}</div>
    <div className="feedback-topics"><div className="feedback-topics-heading"><h4>Темы отзывов с жалобами</h4><span>Оценки 1–3★</span></div>{topics.length?topics.map(([key,count])=><div className="feedback-topic" key={key}><span>{(topicLabels as Record<string,string>)[key]||key}</span><b>{number(count)}</b></div>):<p className="muted">{current.total===0?'За этот период отзывов нет.':current.negative===0?'Отзывов с оценкой 1–3 звезды нет.':'Темы в отзывах пока не определены.'}</p>}{topics.length>0&&<small>Один отзыв может относиться к нескольким темам.</small>}</div>
   </>:<p role="status" className="muted">Готовим статистику выбранного товара…</p>}
   <div className="feedback-actions"><button className="primary" onClick={()=>onReviews(true)}>Читать отзывы 1–3★ <span aria-hidden="true">→</span></button><button onClick={()=>onReviews(false)}>Все отзывы товара</button></div>
  </>}
 </section>;
}

export default function ProductFeedbackInsights({stats,storeId,onAttention,onReviews}:{stats:Row;storeId:string;onAttention:()=>void;onReviews:(product:Product,negative:boolean)=>void}){
 const [product,setProduct]=useState<Product|null>(null),[data,setData]=useState<Stats|null>(null),[loading,setLoading]=useState(false),[error,setError]=useState(''),[retry,setRetry]=useState(0);
 const panel=useRef<HTMLDivElement>(null),days=Number(stats.period_days||0);
 useEffect(()=>{
  let active=true;
  setData(null);setError('');setLoading(Boolean(product));
  if(product){
   const id=product.id;
   api('/analytics?days='+days+'&product_id='+encodeURIComponent(id),'GET',undefined,30000).then(result=>{
    if(!active)return;
    if(result.product_id!==id||result.period_days!==days)throw new Error('Получены данные для другого товара или периода. Повторите загрузку.');
    setData(result);
   }).catch(reason=>{if(active)setError(reason instanceof Error?reason.message:'Повторите загрузку немного позже.')}).finally(()=>{if(active)setLoading(false)});
  }
  return()=>{active=false};
 },[product?.id,days,storeId,retry]);
 function select(value:Product){setProduct(value);setRetry(current=>current+1);panel.current?.scrollIntoView({block:'start',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});panel.current?.querySelector('h3')?.focus({preventScroll:true})}
 return <div className="product-feedback-insights"><div className="two-columns feedback-overview"><section className="card feedback-store-topics"><div className="product-feedback-heading"><div><span className="feedback-eyebrow">ВЫБРАННЫЙ МАГАЗИН</span><h3>Темы обратной связи</h3></div></div><p className="muted">Все товары магазина · {period(days).toLowerCase()}</p>{Object.entries(stats.topics||{}).length?Object.entries(stats.topics).sort((a,b)=>Number(b[1])-Number(a[1])).map(([key,count])=><div className="topic" key={key}><span>{(topicLabels as Record<string,string>)[key]||key}</span><div><i style={{width:Math.max(2,Number(count)/Math.max(1,stats.total)*100)+'%'}}/></div><b>{number(Number(count))}</b></div>):<p className="muted">{stats.total===undefined?'Темы появятся после загрузки сводки.':'Темы отзывов пока не определены.'}</p>}</section><div ref={panel} className="feedback-panel-anchor"><ProductFeedbackCard product={product} data={data} days={days} loading={loading} error={error} onRetry={()=>setRetry(value=>value+1)} onClear={()=>setProduct(null)} onReviews={negative=>{if(product)onReviews(product,negative)}}/></div></div><ProblemProducts products={stats.problem_products||[]} loading={stats.total===undefined} selectedId={product?.id} onSelect={select} onAttention={onAttention}/></div>;
}
