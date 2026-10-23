'use client';
import {AnimatedNumber} from './WorkspaceMotion';
import AppIcon from './AppIcon';
import './marketplace-sync.css';

type Row = Record<string, any>;

export default function MarketplaceConnection({store,stores,name,connected,owner,canManage,busy,token,setToken,config,setConfig,status,onSelect,onSave,onTest,onSync,onHistory,productSync,reviewSync,questionSync}:Row) {
  const items=[{kind:"products",label:"Товары",icon:"Товары",info:productSync},{kind:"reviews",label:"Отзывы",icon:"Отзывы",info:reviewSync},{kind:"questions",label:"Вопросы",icon:"Вопросы",info:questionSync}];
  return <div className="card marketplace-connection">
    <div className="card-heading"><div><h3>{name}</h3><p className="muted">Подключение активного магазина и обмен с маркетплейсом</p></div><span className={'pill '+(connected?'green':'amber')}>{connected?'Подключён':'Не подключён'}</span></div>
    <label>Магазин<select value={stores.active||''} disabled={busy} onChange={e=>onSelect(e.target.value)}><option value="" disabled>Выберите магазин</option>{stores.items?.map((item:Row)=><option key={item.id} value={item.id}>{item.name} · {item.provider==='wb'?'Wildberries':item.provider==='ozon'?'Ozon':'Яндекс Маркет'}{item.can_operate===false?' · просмотр':''}</option>)}</select></label>
    {owner&&<>
      {store.provider==='ozon'&&<label>Client-Id Ozon<input value={config.client_id??store.config?.client_id??''} onChange={e=>setConfig({...config,client_id:e.target.value})} placeholder="Client-Id кабинета"/></label>}
      {store.provider==='yandex'&&<label>Business ID Яндекс Маркета<input value={config.business_id??store.config?.business_id??''} onChange={e=>setConfig({...config,business_id:e.target.value})} placeholder="Числовой ID кабинета"/></label>}
      <label>API-ключ {name}<input type="password" autoComplete="off" value={token} onChange={e=>setToken(e.target.value)} placeholder={connected?'Ключ сохранён; введите новый для замены':'Вставьте API-ключ'}/></label>
      <div className="connection-actions"><button className="primary" disabled={busy||!store.id||token.length<10||(store.provider==='ozon'&&!(config.client_id??store.config?.client_id))||(store.provider==='yandex'&&!(config.business_id??store.config?.business_id))} onClick={onSave}>{status.state==='working'&&status.kind==='token'?'Сохраняю…':'Сохранить подключение'}</button></div>
    </>}
    <div className="button-row"><button disabled={busy||!canManage||!connected} onClick={onTest}>{status.state==='working'&&status.kind==='test'?'Проверяю…':'Проверить соединение'}</button></div>
    <section className="marketplace-sync" aria-label="Синхронизация магазина">
      <div className="marketplace-sync-heading"><div><h3>Синхронизация</h3><p className="muted">Данные магазина в одном месте</p></div><span className="sync-live-label"><i/> Обновляется автоматически</span></div>
      <div className="marketplace-sync-grid">{items.map(({kind,label,icon,info}:Row)=>{
        const local=status.state==='working'&&status.kind===kind;
        const working=local||info.tone==='working';
        const queued=local?status.phase==='queued':info.state==='queued';
        const progress=Number(local?status.progress:info.progress)||0;
        const tone=working?'working':info.tone;
        const catalog=kind==='products';
        const processed=Number(local?status.processed:info.processed)||0;
        const phaseLabel=info.phase==='archived'?'Загружаем архивные товары':info.phase==='missing'?'Уточняем карточки из истории':'Загружаем каталог';
        return <article className={'marketplace-sync-card '+tone} key={kind} aria-busy={working}>
          <div className="sync-card-top"><span className="sync-card-icon"><AppIcon name={icon}/></span><h4>{label}</h4><span className={'sync-state '+tone}>{working?(queued?'В очереди':'Загружаем'):tone==='success'?'Обновлено':tone==='error'?'Нужна проверка':'Нет данных'}</span></div>
          <div className="sync-card-result">{working?<><strong>{queued?'Ожидает запуска':catalog?<AnimatedNumber value={processed}/>:progress+'%'}</strong><div className={'sync-card-progress '+(queued||catalog?'queued':'')} role="progressbar" aria-label={'Синхронизация: '+label} aria-valuemin={0} aria-valuemax={100} {...(!queued&&!catalog?{'aria-valuenow':progress}:{})}><i style={{width:queued||catalog?'28%':Math.max(2,Math.min(100,progress))+'%'}}/></div><small>{queued?'Начнётся после текущего задания':catalog?phaseLabel+' · обработано карточек':'Можно продолжать работу на сайте'}</small>{catalog&&info.updatedAt&&<time>Прогресс обновлён: {new Date(info.updatedAt).toLocaleTimeString('ru-RU')}</time>}</>:<><strong>{tone==='success'?<AnimatedNumber value={info.count}/>:tone==='error'?'Не завершена':'Ещё не загружались'}</strong><small>{tone==='success'?'Записей обработано':info.detail}</small>{tone==='success'&&<time>{info.title}</time>}</>}</div>
          <button className="sync-card-button" disabled={busy||!canManage||!connected||working} onClick={()=>onSync(kind)} aria-label={'Синхронизировать '+label.toLowerCase()}><span className={working?'sync-rotate':''}>↻</span>{working?(queued?'Ожидаем запуска':'Синхронизация…'):'Обновить '+label.toLowerCase()}</button>
        </article>;
      })}</div>
    </section>
    {store.provider==='yandex'&&<p className="muted">Загрузка каталога обновляет названия, фотографии, описания и артикулы товаров. Для API-ключа нужен доступ «Просмотр товаров и карточек».</p>}
    {store.provider==='ozon'&&<p className="muted">Загрузка каталога обновляет названия, фотографии и характеристики товаров. Для полных названий характеристик API-ключу нужен доступ к справочникам категорий Ozon.</p>}
    {store.provider==='ozon'&&<p className="muted">Доступ к вопросам Ozon зависит от прав и возможностей кабинета. При отказе API проверьте доступ в Ozon Seller.</p>}
    {status.message&&<div className={'wb-feedback '+status.state} role={status.state==='error'?'alert':'status'}><span>{status.state==='success'?'✓':status.state==='error'?'!':'↻'}</span><div><b>{status.message}</b>{status.state==='working'&&<div className={status.kind==='products'?'sync-card-progress queued':'job-progress'}><i style={{width:status.kind==='products'?'28%':(status.progress||5)+'%'}}/></div>}</div></div>}

    <button className="sync-details" onClick={onHistory}>Открыть историю синхронизаций →</button>
  </div>;
}
