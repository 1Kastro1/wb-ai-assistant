'use client';
import {AnimatedNumber} from './WorkspaceMotion';
import AppIcon from './AppIcon';
import ActionButton from './ActionButton';

export default function HomeWelcome({store,busy,onReviews,onQueue,onApproval,onRefresh}:{store:string;busy:boolean;onReviews:()=>void;onQueue:()=>void;onApproval:()=>void;onRefresh:()=>Promise<boolean>}){
 return <div className="home-welcome ops-welcome"><div className="home-welcome-copy"><span className="home-kicker"><span/>ОБЗОР РАБОЧЕГО ПРОСТРАНСТВА</span><h1>Рабочий день магазина</h1><p>Новые обращения, ответы на проверку и текущие задания.</p><span className="home-store-caption">Выбран магазин: <b>{store||'Магазин не выбран'}</b></span></div><div className="home-welcome-actions"><button className="primary" disabled={busy} onClick={onReviews}><AppIcon name="Отзывы"/>Открыть отзывы<span aria-hidden="true">→</span></button><ActionButton label="Обновить данные" pendingLabel="Обновляем…" successLabel="Обновлено" disabled={busy} onAction={onRefresh}/></div></div>;
}

export function HomeQualityScore({score}:{score?:number}){
 const known=typeof score==='number';
 const value=Math.max(0,Math.min(100,Number(score)||0));
 return <div className="home-quality"><svg viewBox="0 0 120 120" role="img" aria-label={known?'Средняя оценка качества: '+value+' из 100':'Оценка качества пока не загружена'}><circle cx="60" cy="60" r="49" fill="none" stroke="#edf0f8" strokeWidth="8"/><circle cx="60" cy="60" r="49" fill="none" stroke="#7e57cd" strokeWidth="8" strokeLinecap="round" pathLength="100" strokeDasharray={`${value} 100`} transform="rotate(-90 60 60)"/></svg><div><strong><AnimatedNumber value={known?value:undefined}/></strong><span>из 100</span></div><p>Средняя оценка<br/>подготовленных ответов</p></div>;
}
