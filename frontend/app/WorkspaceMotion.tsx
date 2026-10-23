'use client';
import {useEffect,useRef,useState} from 'react';
const reducedMotion=()=>window.matchMedia('(prefers-reduced-motion: reduce)');

export function WorkspaceBackdrop(){
  useEffect(()=>{
    const preference=reducedMotion();
    const update=()=>{document.documentElement.dataset.motion=preference.matches?'off':'on'};
    update();preference.addEventListener('change',update);
    return()=>preference.removeEventListener('change',update);
  },[]);
  return <div className="workspace-atmosphere" aria-hidden="true"><i className="ambient ambient-violet"/><i className="ambient ambient-mint"/><div className="ambient-grid"/></div>;
}

// Animate the existing surface without remounting editors or resetting their state.
export function WorkspaceTransition({section,store,loading}:{section:string;store:string;loading:boolean}){
  useEffect(()=>{
    const preference=reducedMotion(),surface=document.querySelector<HTMLElement>('.main>.body');
    if(loading||preference.matches||!surface?.animate)return;
    const animation=surface.animate([{opacity:.45,transform:'translateY(7px)'},{opacity:1,transform:'translateY(0)'}],{duration:240,easing:'cubic-bezier(.2,.75,.25,1)'});
    const stop=()=>{if(preference.matches)animation.cancel()};
    preference.addEventListener('change',stop);
    return()=>{animation.cancel();preference.removeEventListener('change',stop)};
  },[section,store,loading]);
  return null;
}

// Accessible text always contains the actual value, never interpolated frames.
export function AnimatedNumber({value,digits=0}:{value:number|null|undefined;digits?:number}){
  const known=typeof value==='number'&&Number.isFinite(value),next=known?value:null;
  const [display,setDisplay]=useState<number|null>(next),displayed=useRef<number|null>(next);
  useEffect(()=>{
    const preference=reducedMotion();let frame=0;const from=displayed.current;
    const finish=()=>{cancelAnimationFrame(frame);displayed.current=next;setDisplay(next)};
    if(next===null||from===null||from===next||preference.matches){finish();return;}
    const started=performance.now();
    const tick=(time:number)=>{
      const progress=Math.min(1,Math.max(0,(time-started)/420));
      displayed.current=from+(next-from)*(1-Math.pow(1-progress,3));setDisplay(displayed.current);
      if(progress<1)frame=requestAnimationFrame(tick);
    };
    frame=requestAnimationFrame(tick);
    const changed=()=>{if(preference.matches)finish()};preference.addEventListener('change',changed);
    return()=>{cancelAnimationFrame(frame);preference.removeEventListener('change',changed)};
  },[next]);
  const format=(number:number|null)=>number===null?'—':number.toLocaleString('ru-RU',{minimumFractionDigits:0,maximumFractionDigits:digits});
  return <span className="animated-number"><span aria-hidden="true">{format(known?display:null)}</span><span className="sr-only">{format(next)}</span></span>;
}
