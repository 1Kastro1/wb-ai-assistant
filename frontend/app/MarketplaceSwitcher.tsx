'use client';

type Store = {id:string; name:string; provider:string; connected?:boolean; can_operate?:boolean};

const label:Record<string,string> = {wb:'Wildberries',ozon:'Ozon',yandex:'Яндекс Маркет'};

export default function MarketplaceSwitcher({stores,active,busy,onSelect}:{stores:Store[];active:string;busy:boolean;onSelect:(id:string)=>void}) {
  return <div className="market-switcher" aria-label="Выбор маркетплейса">
    <p className="nav-label">МАГАЗИНЫ</p>
    {!stores.length&&<p className="muted">Магазины пока не добавлены.</p>}
    {stores.map(store => <button key={store.id} type="button" className={active===store.id?'active':''} disabled={busy} onClick={()=>onSelect(store.id)} aria-current={active===store.id?'page':undefined}>
      <span className={'market-symbol '+store.provider}>{store.provider==='wb'?'W':store.provider==='ozon'?'O':'Я'}</span>
      <span className="market-copy"><b>{label[store.provider]||store.provider}</b><small>{store.name}{store.can_operate===false?' · просмотр':''}</small></span>
      <span className={'market-connection '+(store.connected?'connected':'')} title={store.connected?'API подключён':'API не подключён'} />
    </button>)}
  </div>;
}
