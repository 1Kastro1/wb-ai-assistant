'use client';

type Counts = {reviews:number;reviews_unanswered:number;reviews_negative:number;reviews_answered:number;reviews_drafts:number;questions:number;questions_unanswered:number;questions_answered:number;questions_drafts:number};
type SyncState = {status:string;started_at:string;progress:number;processed?:number;message?:string}|null;
type Store = Counts & {id:string;name:string;provider:string;connected:boolean;can_operate?:boolean;sync?:Record<string,SyncState>};
type TrendDay = {date:string;reviews:number;questions:number;negative:number};
type Overview = {days:number;totals:Counts;stores:Store[];trend?:TrendDay[]};

const number=(value:number)=>Number(value||0).toLocaleString('ru-RU');
const names:Record<string,string> = {wb:'Wildberries',ozon:'Ozon',yandex:'Яндекс Маркет'};
const empty:Counts = {reviews:0,reviews_unanswered:0,reviews_negative:0,reviews_answered:0,reviews_drafts:0,questions:0,questions_unanswered:0,questions_answered:0,questions_drafts:0};

export default function MarketplaceOverview({overview,busy,onPeriod,onSelect}:{overview:Overview|null;busy:boolean;onPeriod:(days:number)=>void;onSelect:(id:string)=>void}) {
  const totals = overview?.totals||empty;
  const pending = totals.reviews_unanswered + totals.questions_unanswered;
  const trend=overview?.trend||[];
  const peak=Math.max(1,...trend.map(day=>day.reviews+day.questions));
  return <section className="market-overview">
    <div className="section-title"><div><p className="eyebrow">ВСЕ МАРКЕТПЛЕЙСЫ</p><h2>Все магазины — один обзор</h2><p className="muted">Данные, полученные при синхронизации магазинов. Отключённые магазины сохраняют свою историю.</p></div><div className="button-row"><button className={overview?.days===0?'active':''} disabled={busy} onClick={()=>onPeriod(0)}>За всё время</button><button className={overview?.days===30?'active':''} disabled={busy} onClick={()=>onPeriod(30)}>30 дней</button></div></div>
    <div className="metrics market-totals"><div className="metric"><span>Отзывы</span><strong>{overview?number(totals.reviews): '—'}</strong><small>{totals.reviews_unanswered} без ответа</small></div><div className="metric"><span>Вопросы</span><strong>{overview?number(totals.questions): '—'}</strong><small>{totals.questions_unanswered} без ответа</small></div><div className="metric"><span>Требуют ответа</span><strong>{overview?number(pending): '—'}</strong><small>Отзывы и вопросы</small></div><div className="metric"><span>Черновики</span><strong>{overview?number(totals.reviews_drafts+totals.questions_drafts): '—'}</strong><small>Для проверки командой</small></div></div>
    {!!trend.length&&<div className="card marketplace-trend"><h3>Обращения покупателей за 14 дней</h3><p className="muted">Отзывы — фиолетовые, вопросы — голубые. Негативных отзывов за период: {trend.reduce((sum,day)=>sum+day.negative,0)}.</p><div className="marketplace-trend-bars">{trend.map(day=><div key={day.date} title={`${day.date}: ${day.reviews} отзывов, ${day.questions} вопросов, ${day.negative} негативных`}><div className="marketplace-trend-stack" style={{height:`${Math.max(4,(day.reviews+day.questions)/peak*100)}%`}}><i style={{flex:day.reviews||0,background:'#7457de'}}/><i style={{flex:day.questions||0,background:'#79b7e8'}}/></div><small>{day.date.slice(8)}</small></div>)}</div></div>}
    <div className="market-overview-grid">{overview?.stores.map(store=>{
      const responseRate=store.reviews?Math.round(store.reviews_answered/store.reviews*100):0;
      return <article className={'card market-overview-card '+store.provider} key={store.id}>
        <div className="card-heading"><div><span className="market-provider">{names[store.provider]||store.provider}</span><h3>{store.name}</h3></div><span className={'pill '+(store.connected?'green':'amber')}>{store.connected?'API подключён':'Нет API-ключа'}</span></div>
        <div className="market-overview-numbers"><div><strong>{number(store.reviews)}</strong><small>отзывов</small></div><div><strong>{number(store.questions)}</strong><small>вопросов</small></div></div>
        <div className="market-progress"><span>Ответы на отзывы</span><b>{responseRate}%</b><div><i style={{width:responseRate+'%'}}/></div></div>
        <p className="muted">Без ответа: {store.reviews_unanswered} отзывов и {store.questions_unanswered} вопросов · негативных отзывов: {store.reviews_negative} · черновиков: {store.reviews_drafts+store.questions_drafts}</p>
        <div className="market-sync"><b>Последняя синхронизация</b>{(['reviews','questions','products'] as const).map(kind=>{const job=store.sync?.[kind];return <small key={kind}>{kind==='reviews'?'Отзывы':kind==='questions'?'Вопросы':'Товары'}: {!store.connected?'API не подключён':!job?'ещё не запускалась':`${job.status==='completed'?'успешно':job.status==='failed'?'ошибка':job.status==='running'?'выполняется':job.status==='queued'?'ожидает':'остановлена'} · ${new Date(job.started_at).toLocaleString('ru')}${job.status==='completed'&&job.processed!==undefined?` · получено ${job.processed}`:''}${job.status==='running'?(kind==='products'?` · обработано ${number(job.processed||0)}`:` · ${job.progress}%`):''}`} {job?.status==='failed'&&job.message&&<span title={job.message}>ⓘ Причина</span>}</small>})}</div>
        <button className="full" disabled={busy} onClick={()=>onSelect(store.id)}>{store.can_operate===false?'Открыть для просмотра →':'Открыть магазин →'}</button>
      </article>
    })}</div>
  </section>;
}
