'use client';
import {useEffect,useRef,useState} from 'react';
import {createButtonAction} from './button-action.mjs';

export default function ActionButton({label,pendingLabel='Выполняем…',successLabel='Готово',disabled=false,className='',onAction}:{label:string;pendingLabel?:string;successLabel?:string;disabled?:boolean;className?:string;onAction:()=>Promise<boolean>}){
 const [state,setState]=useState('idle'),mounted=useRef(true);
 const action=useRef<ReturnType<typeof createButtonAction>|null>(null);
 if(!action.current)action.current=createButtonAction((next:string)=>{if(mounted.current)setState(next)});
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[]);
 useEffect(()=>{if(state!=='success')return;const timer=setTimeout(()=>setState('idle'),2200);return()=>clearTimeout(timer)},[state]);
 const pending=state==='pending';
 return <button type="button" className={'action-button '+className} data-state={state} aria-label={label} aria-busy={pending} disabled={disabled||pending} onClick={()=>void action.current?.(onAction)}><span className="action-label-reserve" aria-hidden="true">{label}</span><span className="action-content"><span aria-hidden="true" className="action-symbol">{pending?<i className="button-spinner"/>:state==='success'?'✓':state==='error'?'↻':null}</span><span aria-live="polite">{pending?pendingLabel:state==='success'?successLabel:state==='error'?'Повторить':label}</span></span></button>;
}
