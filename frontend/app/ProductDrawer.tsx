'use client';
import {ReactNode,useEffect,useId,useRef} from 'react';

export default function ProductDrawer({name,busy=false,error='',onClose,children}:{name:string;busy?:boolean;error?:string;onClose:()=>void;children:ReactNode}){
 const dialog=useRef<HTMLDialogElement>(null),title=useId();
 useEffect(()=>{
  const element=dialog.current,trigger=document.activeElement as HTMLElement|null,overflow=document.body.style.overflow;
  if(!element)return;
  element.showModal();document.body.style.overflow='hidden';
  return()=>{element.close();document.body.style.overflow=overflow;if(trigger?.isConnected)trigger.focus({preventScroll:true})};
 },[]);
 return <dialog ref={dialog} className="product-drawer" aria-labelledby={title} onCancel={event=>{event.preventDefault();if(!busy)onClose()}} onClick={event=>{if(event.target!==event.currentTarget||busy)return;const bounds=event.currentTarget.getBoundingClientRect();if(event.clientX<bounds.left||event.clientX>bounds.right||event.clientY<bounds.top||event.clientY>bounds.bottom)onClose()}}><div className="drawer-heading"><div><span className="eyebrow">СВЕДЕНИЯ О ТОВАРЕ</span><h2 id={title}>Карточка товара</h2><p>{name}</p></div><button type="button" autoFocus disabled={busy} onClick={onClose} aria-label="Закрыть карточку">Закрыть <span aria-hidden="true">×</span></button></div><div className="drawer-content">{busy&&<p className="drawer-status" role="status">Сохраняем изменения или загружаем сведения…</p>}{error&&<div className="error" role="alert">{error}</div>}{children}</div></dialog>;
}
