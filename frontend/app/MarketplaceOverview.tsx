'use client';

type Counts = {reviews:number;reviews_unanswered:number;reviews_negative:number;reviews_answered:number;reviews_drafts:number;questions:number;questions_unanswered:number;questions_answered:number;questions_drafts:number};
type Store = Counts & {id:string;name:string;provider:string;connected:boolean};
type Overview = {days:number;totals:Counts;stores:Store[]};

const names:Record<string,string> = {wb:'Wildberries',ozon:'Ozon',yandex:'Яндекс Маркет'};
const empty:Counts = {reviews:0,reviews_unanswered:0,reviews_negative:0,reviews_answered:0,reviews_drafts:0,questions:0,questions_unanswered:0,questions_answered:0,questions_drafts:0};

export default function MarketplaceOverview({overview,busy,onPeriod,onSelect}:{overview:Overview|null;busy:boolean;onPeriod:(days:number)=>void;onSelect:(id:string)=>void}) {
  const totals = overview?.totals||empty;
  const pending = totals.reviews_unanswered + totals.questions_unanswered;
  return <section className="market-overview">
    <div className="section-title"><div><p className="eyebrow">ВСЕ МАРКЕТПЛЕЙСЫ</p><h1>Общая статистика</h1><p className="muted">Данные, полученные при синхронизации магазинов. Отключённые магазины сохраняют свою историю.</p></div><div className="button-row"><button className={overview?.days===0?'active':''} disabled={busy} onClick={()=>onPeriod(0)}>За всё время</button><button className={overview?.days===30?'active':''} disabled={busy} onClick={()=>onPeriod(30)}>30 дней</button></div></div>
    <div className="metrics market-totals"><div className="metric"><span>Отзывы</span><strong>{totals.reviews}</strong><small>{totals.reviews_unanswered} без ответа</small></div><div className="metric"><span>Вопросы</span><strong>{totals.questions}</strong><small>{totals.questions_unanswered} без ответа</small></div><div className="metric"><span>Требуют ответа</span><strong>{pending}</strong><small>Отзывы и вопросы</small></div><div className="metric"><span>Черновики</span><strong>{totals.reviews_drafts+totals.questions_drafts}</strong><small>Для проверки командой</small></div></div>
    <div className="market-overview-grid">{overview?.stores.map(store=>{
      const responseRate=store.reviews?Math.round(store.reviews_answered/store.reviews*100):0;
      return <article className={'card market-overview-card '+store.provider} key={store.id}>
        <div className="card-heading"><div><span className="market-provider">{names[store.provider]||store.provider}</span><h3>{store.name}</h3></div><span className={'pill '+(store.connected?'green':'amber')}>{store.connected?'API подключён':'Нет API-ключа'}</span></div>
        <div className="market-overview-numbers"><div><strong>{store.reviews}</strong><small>отзывов</small></div><div><strong>{store.questions}</strong><small>вопросов</small></div></div>
        <div className="market-progress"><span>Ответы на отзывы</span><b>{responseRate}%</b><div><i style={{width:responseRate+'%'}}/></div></div>
        <p className="muted">Без ответа: {store.reviews_unanswered} отзывов и {store.questions_unanswered} вопросов · негативных отзывов: {store.reviews_negative} · черновиков: {store.reviews_drafts+store.questions_drafts}</p>
        <button className="full" disabled={busy} onClick={()=>onSelect(store.id)}>Открыть магазин →</button>
      </article>
    })}</div>
  </section>;
}
