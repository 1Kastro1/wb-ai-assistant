'use client';
import {useEffect,useRef,useState} from 'react';
import MarketplaceSwitcher from './MarketplaceSwitcher';
import AppIcon,{BrandMark} from './AppIcon';
type Row=Record<string,any>;
export default function AppSidebar({user,stores,page,nav,pending,unread,busy,onPage,onStore,onLogout}:{user:Row;stores:Row;page:string;nav:string[][];pending:number;unread:number;busy:boolean;onPage:(name:string)=>void;onStore:(id:string)=>void;onLogout:()=>void}){
  const [collapsed,setCollapsed]=useState(false);
  const profile=useRef<HTMLDetailsElement>(null);
  useEffect(()=>{try{setCollapsed(localStorage.getItem('sidebar-collapsed')==='true')}catch{}},[]);
  function toggle(){if(profile.current)profile.current.open=false;setCollapsed(value=>{const next=!value;try{localStorage.setItem('sidebar-collapsed',String(next))}catch{}return next})}
  return <aside className={'sidebar '+(collapsed?'is-collapsed':'')}>
    <div className="sidebar-brand-row"><div className="brand"><BrandMark/><div className="sidebar-copy"><b>Ассистент<br/>маркетплейсов</b></div></div><button className="sidebar-toggle" type="button" aria-label={collapsed?'Развернуть меню':'Свернуть меню'} title={collapsed?'Развернуть меню':'Свернуть меню'} aria-expanded={!collapsed} aria-controls="workspace-navigation" onClick={toggle}><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>{collapsed?<path d="m13 9 3 3-3 3"/>:<path d="m16 9-3 3 3 3"/>}</svg></button></div>
    <details ref={profile} className="profile-menu" onToggle={event=>{if(event.currentTarget.open&&collapsed){setCollapsed(false);try{localStorage.setItem("sidebar-collapsed","false")}catch{}}}}><summary title={user.display_name||user.username}><span className="avatar">{(user.display_name||'П').slice(0,1).toUpperCase()}</span><span className="sidebar-copy"><b>{user.display_name||user.username}</b><small>{user.position||(user.role==='owner'?'Владелец':'Менеджер')}</small></span><span className="profile-chevron"><AppIcon name="chevron"/></span></summary><div className="profile-options"><p>{stores.items?.find((s:Row)=>s.id===stores.active)?.name||'Нет назначенного магазина'}</p><button onClick={onLogout}><AppIcon name="logout"/>Выйти из аккаунта</button></div></details>
    <MarketplaceSwitcher stores={stores.items||[]} active={stores.active||''} busy={busy} onSelect={onStore}/>
    <p className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</p><nav id="workspace-navigation" aria-label="Разделы приложения">{nav.map(([icon,name])=><button key={name} className={page===name?'active':''} aria-current={page===name?'page':undefined} title={name==='Память'?'Правила ответов':name} aria-label={name==='Память'?'Правила ответов':name} onClick={()=>onPage(name)}><span className="nav-icon"><AppIcon name={name}/></span><span className="nav-copy">{name==='Память'?'Правила ответов':name}</span>{name==='Действия'&&pending>0&&<em>{pending}</em>}{name==='Уведомления'&&unread>0&&<em>{unread}</em>}</button>)}</nav>
    <div className="sidebar-bottom"><span className="dot"/><span className="sidebar-copy">Рабочее пространство команды</span></div>
  </aside>;
}

