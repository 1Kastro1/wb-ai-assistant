'use client';
import {useEffect,useState} from 'react';
import AppIcon from './AppIcon';
import {scheduleValues,schedulePayload} from './automation-settings.mjs';
import './automation-settings.css';
type Row=Record<string,any>;
export default function AutomationSettings({schedule,onSave,busy=false,assistantEnabled=true}:{schedule:Row;onSave:(value:Row)=>Promise<void>;busy?:boolean;assistantEnabled?:boolean}){
 const saved=scheduleValues(schedule),signature=JSON.stringify(saved);
 const [values,setValues]=useState<Row>(saved),[dirty,setDirty]=useState(false),[saving,setSaving]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 useEffect(()=>{if(!dirty)setValues(JSON.parse(signature));},[signature,dirty]);
 const update=(change:Row)=>{setValues(current=>({...current,...change}));setDirty(true);setError('');setMessage('');};
 const enabled=saved.reviews_sync_enabled||saved.questions_sync_enabled||saved.drafts_enabled||saved.question_drafts_enabled;
 return <form className="automation-settings" onSubmit={async event=>{event.preventDefault();setError('');setMessage('');setSaving(true);try{await onSave(schedulePayload(values));setDirty(false);setMessage('Расписание сохранено на сервере.');}catch(error){setError(error instanceof Error?error.message:'Не удалось сохранить расписание. Повторите попытку.');}finally{setSaving(false);}}}>
  <div className="automation-heading"><div><span className="automation-eyebrow">ВАШЕ РАСПИСАНИЕ</span><h2>Автоматизация</h2><p>Обновление данных и подготовка ответов без вашего участия.</p></div><span className={'automation-status '+(enabled?'is-on':'')}><i/>{enabled?'Работает в фоне':'На паузе'}</span></div>
  <div className="automation-cards">{[
   {key:'reviews',title:'Отзывы',description:'Обновление отзывов и подготовка ответов покупателям.',icon:'Отзывы',sync:'reviews_sync_enabled',generation:'drafts_enabled',syncHours:'reviews_hours',generationHours:'drafts_hours'},
   {key:'questions',title:'Вопросы',description:'Обновление вопросов и подготовка ответов перед покупкой.',icon:'Вопросы',sync:'questions_sync_enabled',generation:'question_drafts_enabled',syncHours:'questions_hours',generationHours:'question_drafts_hours'},
  ].map(item=><section className={'automation-card '+item.key+((values[item.sync]||values[item.generation])?' is-enabled':'')} key={item.key} aria-label={item.title}>
   <div className="automation-card-top"><span className="automation-icon"><AppIcon name={item.icon}/></span></div>
   <h3>{item.title}</h3><p>{item.description}</p>
   {[
    {field:item.sync,interval:item.syncHours,title:'Обновление данных',label:'Интервал обновления: '+item.title.toLowerCase(),generation:false},
    {field:item.generation,interval:item.generationHours,title:'Генерация ответов',label:'Интервал генерации ответов: '+item.title.toLowerCase(),generation:true},
   ].map(control=>{const on=Boolean(values[control.field]);return <div className="automation-task" key={control.field}>
    <div className="automation-task-heading"><b>{control.title}</b><label className="automation-toggle"><input type="checkbox" checked={on} disabled={saving||busy||control.generation&&!assistantEnabled} onChange={event=>update({[control.field]:event.target.checked})} aria-label={'Включить: '+control.title.toLowerCase()+' — '+item.title.toLowerCase()}/><span aria-hidden="true"/></label></div>
    <label className="automation-interval"><span>{control.label}</span><div><span>Каждые</span><input type="number" min="1" max="168" step="1" required inputMode="numeric" value={values[control.interval]} disabled={saving||busy} onChange={event=>update({[control.interval]:event.target.value})}/><span>ч.</span></div></label>
    <div className="automation-card-note"><i className={on?'is-on':''}/>{on?(dirty?'Изменения вступят в силу после сохранения':'По сохранённому расписанию'):'Запуски по расписанию выключены'}</div>
    {control.generation&&!assistantEnabled&&<small className="automation-assistant-note">Для генерации включите ассистента.</small>}
   </div>;})}
  </section>)}</div>
  <div className="automation-background"><span className="automation-background-icon"><AppIcon name="История действий"/></span><div><b>Продолжит работу, даже если закрыть сайт</b><p>Расписание выполняется на сервере для всех подключённых магазинов. Ответы сохраняются как черновики — отправка после вашего подтверждения.</p></div></div>
  <footer className="automation-footer"><span>{dirty?'Есть несохранённые изменения':'Отзывы и вопросы — каждое по своему расписанию'}</span><button className="primary" type="submit" disabled={saving||busy||!dirty}>{saving?'Сохраняем…':'Сохранить расписание'}</button></footer>
  {error&&<p className="automation-feedback error" role="alert">{error}</p>}{message&&<p className="automation-feedback" role="status">{message}</p>}
 </form>;
}
