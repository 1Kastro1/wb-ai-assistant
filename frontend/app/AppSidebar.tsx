'use client';
import {useEffect,useState} from 'react';
import MarketplaceSwitcher from './MarketplaceSwitcher';
type Row=Record<string,any>;
export default function AppSidebar({user,stores,page,nav,pending,unread,busy,onPage,onStore,onLogout}:{user:Row;stores:Row;page:string;nav:string[][];pending:number;unread:number;busy:boolean;onPage:(name:string)=>void;onStore:(id:string)=>void;onLogout:()=>void}){
  const [collapsed,setCollapsed]=useState(false);
  useEffect(()=>{try{setCollapsed(localStorage.getItem('sidebar-collapsed')==='true')}catch{}},[]);
  function toggle(){setCollapsed(value=>{const next=!value;try{localStorage.setItem('sidebar-collapsed',String(next))}catch{}return next})}
  return <aside className={'sidebar '+(collapsed?'is-collapsed':'')}>
    <div className="sidebar-brand-row"><div className="brand"><div className="mark small">М<span>·</span></div><div className="sidebar-copy"><b>Ассистент<br/>маркетплейсов</b><small>Командное пространство</small></div></div><button className="sidebar-toggle" type="button" aria-label={collapsed?'Развернуть меню':'Свернуть меню'} title={collapsed?'Развернуть меню':'Свернуть меню'} aria-expanded={!collapsed} aria-controls="workspace-navigation" onClick={toggle}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>{collapsed?<path d="m13 9 3 3-3 3"/>:<path d="m16 9-3 3 3 3"/>}</svg></button></div>
    <div className="workspace" title={user.display_name||user.username}><span className="avatar">{(user.display_name||'П').slice(0,1).toUpperCase()}</span><div className="sidebar-copy"><b>{user.display_name||user.username}</b><small>{user.position||user.role} · {stores.items?.find((s:Row)=>s.id===stores.active)?.name||'Нет назначенного магазина'}</small></div><span className="dot"/></div>
    <MarketplaceSwitcher stores={stores.items||[]} active={stores.active||''} busy={busy} onSelect={onStore}/>
    <p className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</p><nav id="workspace-navigation" aria-label="Разделы приложения">{nav.map(([icon,name])=><button key={name} className={page===name?'active':''} aria-current={page===name?'page':undefined} title={name} aria-label={name} onClick={()=>onPage(name)}><span className="nav-icon" aria-hidden="true">{icon}</span><span className="nav-copy">{name}</span>{name==='Действия'&&pending>0&&<em>{pending}</em>}{name==='Уведомления'&&unread>0&&<em>{unread}</em>}</button>)}</nav>
    <div className="sidebar-bottom"><div className="privacy"><span className="dot"/> Общая база команды</div><small>API магазина подключён на сервере</small><button className="link sidebar-logout" title="Выйти из аккаунта" aria-label="Выйти из аккаунта" onClick={onLogout}><span aria-hidden="true">↗</span><span className="sidebar-copy">Выйти из аккаунта</span></button></div>
  </aside>;
}
