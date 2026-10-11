'use client';

type Row = Record<string, any>;

export default function MarketplaceConnection({store,stores,name,connected,owner,canManage,busy,token,setToken,config,setConfig,status,onSelect,onSave,onTest,onSync,onHistory,productSync,reviewSync,questionSync}:Row) {
  return <div className="card">
    <div className="card-heading"><div><h3>{name}</h3><p className="muted">Подключение активного магазина и обмен с маркетплейсом</p></div><span className={'pill '+(connected?'green':'amber')}>{connected?'Подключён':'Не подключён'}</span></div>
    <label>Магазин<select value={stores.active||''} onChange={e=>onSelect(e.target.value)}><option value="" disabled>Выберите доступный магазин</option>{stores.items?.map((item:Row)=><option key={item.id} value={item.id} disabled={item.accessible===false}>{item.name} · {item.provider==='wb'?'Wildberries':item.provider==='ozon'?'Ozon':'Яндекс Маркет'}{item.accessible===false?' · нет доступа':''}</option>)}</select></label>
    {owner&&<>
      {store.provider==='ozon'&&<label>Client-Id Ozon<input value={config.client_id??store.config?.client_id??''} onChange={e=>setConfig({...config,client_id:e.target.value})} placeholder="Client-Id кабинета"/></label>}
      {store.provider==='yandex'&&<label>Business ID Яндекс Маркета<input value={config.business_id??store.config?.business_id??''} onChange={e=>setConfig({...config,business_id:e.target.value})} placeholder="Числовой ID кабинета"/></label>}
      <label>API-ключ {name}<input type="password" autoComplete="off" value={token} onChange={e=>setToken(e.target.value)} placeholder={connected?'Ключ сохранён; введите новый для замены':'Вставьте API-ключ'}/></label>
      <button className="primary" disabled={busy||token.length<10||(store.provider==='ozon'&&!(config.client_id??store.config?.client_id))||(store.provider==='yandex'&&!(config.business_id??store.config?.business_id))} onClick={onSave}>{status.state==='working'&&status.kind==='token'?'Сохраняю…':'Сохранить подключение'}</button>
    </>}
    <div className="button-row"><button disabled={busy||!canManage||!connected} onClick={onTest}>{status.state==='working'&&status.kind==='test'?'Проверяю…':'Проверить соединение'}</button></div>
    <hr/>
    <div className="button-row">
      <button disabled={busy||!canManage||!connected||store.provider!=='wb'} onClick={()=>onSync('products')}>{status.state==='working'&&status.kind==='products'?'Синхронизация '+(status.progress||0)+'%':'Синхронизировать товары'}</button>
      <button disabled={busy||!canManage||!connected} onClick={()=>onSync('reviews')}>{status.state==='working'&&status.kind==='reviews'?'Синхронизация '+(status.progress||0)+'%':'Синхронизировать отзывы'}</button>
      <button disabled={busy||!canManage||!connected} onClick={()=>onSync('questions')}>{status.state==='working'&&status.kind==='questions'?'Синхронизация '+(status.progress||0)+'%':'Синхронизировать вопросы'}</button>
    </div>
    {store.provider!=='wb'&&<p className="muted">Товары Ozon и Яндекс Маркета добавляются из полученных отзывов и вопросов. Отдельная загрузка каталога пока недоступна.</p>}
    {store.provider==='ozon'&&<p className="muted">Доступ к вопросам Ozon зависит от прав и возможностей кабинета. При отказе API проверьте доступ в Ozon Seller.</p>}
    {status.message&&<div className={'wb-feedback '+status.state} role={status.state==='error'?'alert':'status'}><span>{status.state==='success'?'✓':status.state==='error'?'!':'↻'}</span><div><b>{status.message}</b>{status.state==='working'&&<div className="job-progress"><i style={{width:(status.progress||5)+'%'}}/></div>}</div></div>}
    <div className="sync-overview"><div className={'sync-result '+productSync.tone}><small>Товары</small><b>{productSync.title}</b><span>{productSync.detail}</span></div><div className={'sync-result '+reviewSync.tone}><small>Отзывы</small><b>{reviewSync.title}</b><span>{reviewSync.detail}</span></div><div className={'sync-result '+questionSync.tone}><small>Вопросы</small><b>{questionSync.title}</b><span>{questionSync.detail}</span></div></div>
    <button className="sync-details" onClick={onHistory}>Открыть историю синхронизаций →</button>
  </div>;
}
