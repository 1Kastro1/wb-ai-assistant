'use client';
import {useEffect,useState} from 'react';
import {api} from './client';
type Row=Record<string,any>;
const methods:Row={local_positive:'Проверенный шаблон для оценки без текста',local_review_case:'Проверенный сценарий ответа',exact_input_cache:'Ранее проверенный ответ с теми же данными',semantic:'Проверка моделью'};
export default function RuleDetails({draftId,revision}:{draftId:string;revision?:number}){
 const [data,setData]=useState<Row|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false),[opened,setOpened]=useState(false),[attempt,setAttempt]=useState(0);
 useEffect(()=>{setData(null);setError('')},[draftId,revision]);
 useEffect(()=>{if(!opened)return;let current=true;setLoading(true);setError('');setData(null);api('/drafts/'+encodeURIComponent(draftId)+'/rules').then(result=>{if(current)setData(result)}).catch(e=>{if(current)setError(e.message||'Не удалось загрузить правила')}).finally(()=>{if(current)setLoading(false)});return()=>{current=false}},[opened,draftId,revision,attempt]);
 return <details className="reply-details" onToggle={e=>setOpened(e.currentTarget.open)}><summary>Какие правила применены</summary>{loading&&<p role="status">Загружаем правила…</p>}{error&&<p role="alert" className="orange">{error} <button onClick={()=>setAttempt(value=>value+1)}>Повторить загрузку</button></p>}{data&&<><p>{data.passed?'Проверка сохранённого ответа завершена.':'Ответ требует проверки по текущим правилам.'} Правил: {data.matched}.</p>{data.method&&<p className="muted">{methods[data.method]||'Проверка правил ответа'}</p>}<ul>{data.rules.map((rule:Row,index:number)=><li key={index}><b>{rule.status==='verified'?'Проверено':rule.status==='not_applicable'?'Условие не относится к этому обращению':'Ожидает проверки'}</b><p style={{whiteSpace:'pre-wrap'}}>{rule.text}</p><small>{rule.reason}</small></li>)}</ul></>}</details>
}
