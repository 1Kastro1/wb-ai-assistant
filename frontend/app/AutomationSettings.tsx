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
 const enabled=saved.sync_enabled||saved.generation_enabled;
 return <form className="automation-settings" onSubmit={async event=>{event.preventDefault();setError('');setMessage('');setSaving(true);try{await onSave(schedulePayload(values));setDirty(false);setMessage('Расписание сохранено на сервере.');}catch(error){setError(error instanceof Error?error.message:'Не удалось сохранить расписание. Повторите попытку.');}finally{setSaving(false);}}}>
  <div className="automation-heading"><div><span className="automation-eyebrow">ВАШЕ РАСПИСАНИЕ</span><h2>Автоматизация</h2><p>Обновление данных и подготовка ответов без вашего участия.</p></div><span className={'automation-status '+(enabled?'is-on':'')}><i/>{enabled?'Работает в фоне':'На паузе'}</span></div>
  <div className="automation-cards">{[
   {key:'sync',title:'Обновление данных',description:'Отзывы и вопросы появляются в приложении автоматически.',icon:'Вопросы',interval:'sync_hours',label:'Интервал обновления отзывов и вопросов'},
   {key:'generation',title:'Генерация ответов',description:'Готовые черновики для новых отзывов и вопросов.',icon:'Ассистент',interval:'drafts_hours',label:'Интервал генерации ответов'},
  ].map(item=>{const field=item.key+'_enabled',on=Boolean(values[field]);return <section className={'automation-card '+item.key+(on?' is-enabled':'')} key={item.key} aria-label={item.title}>
   <div className="automation-card-top"><span className="automation-icon"><AppIcon name={item.icon}/></span><label className="automation-toggle"><input type="checkbox" checked={on} disabled={saving||busy||item.key==='generation'&&!assistantEnabled} onChange={event=>update({[field]:event.target.checked})} aria-label={'Включить: '+item.title.toLowerCase()}/><span aria-hidden="true"/></label></div>
   <h3>{item.title}</h3><p>{item.description}</p><label className="automation-interval"><span>{item.label}</span><div><span>Каждые</span><input type="number" min="1" max="168" step="1" required inputMode="numeric" value={values[item.interval]} disabled={saving||busy} onChange={event=>update({[item.interval]:event.target.value})}/><span>ч.</span></div></label>
   <div className="automation-card-note"><i className={on?'is-on':''}/>{on?(dirty?'Изменения вступят в силу после сохранения':'По сохранённому расписанию'):'Запуски по расписанию выключены'}</div>{item.key==='generation'&&!assistantEnabled&&<small className="automation-assistant-note">Для генерации включите ассистента.</small>}
  </section>;})}</div>
  <div className="automation-background"><span className="automation-background-icon"><AppIcon name="История действий"/></span><div><b>Продолжит работу, даже если закрыть сайт</b><p>Расписание выполняется на сервере для всех подключённых магазинов. Ответы сохраняются как черновики — отправка после вашего подтверждения.</p></div></div>
  <footer className="automation-footer"><span>{dirty?'Есть несохранённые изменения':'Два интервала — одно понятное расписание'}</span><button className="primary" type="submit" disabled={saving||busy||!dirty}>{saving?'Сохраняем…':'Сохранить расписание'}</button></footer>
  {error&&<p className="automation-feedback error" role="alert">{error}</p>}{message&&<p className="automation-feedback" role="status">{message}</p>}
 </form>;
}
