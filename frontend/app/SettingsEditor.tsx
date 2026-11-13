'use client';
import {useEffect,useState,ReactNode} from 'react';
import {api} from './client';
type Row=Record<string,any>;
export function settingsDirty(id:string,dirty:boolean){window.dispatchEvent(new CustomEvent('settings-dirty',{detail:{id,dirty}}))}
export default function SettingsEditor({title,description,scope='Для всего приложения',resource,initial,children,onSaved,disabled=false}:{title:string;description?:string;scope?:string;resource:string;initial:Row;children:(value:Row,change:(patch:Row)=>void)=>ReactNode;onSaved?:(result:Row,submitted:Row)=>void;disabled?:boolean}){
 const signature=JSON.stringify(initial),[value,setValue]=useState(initial),[dirty,setDirty]=useState(false),[saving,setSaving]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState(false);
 useEffect(()=>{if(!dirty)setValue(JSON.parse(signature))},[signature,dirty]);
 useEffect(()=>{settingsDirty(resource,dirty);return()=>settingsDirty(resource,false)},[resource,dirty]);
 useEffect(()=>{const reset=()=>{setValue(JSON.parse(signature));setDirty(false);setError('')};window.addEventListener('settings-discard',reset);return()=>window.removeEventListener('settings-discard',reset)},[signature]);
 return <form className="card settings-editor" onSubmit={async e=>{e.preventDefault();setSaving(true);setError('');try{const result=await api(resource,'PATCH',value);onSaved?.(result,value);setDirty(false);setSaved(true)}catch(e){setError(e instanceof Error?e.message:'Не удалось сохранить изменения')}finally{setSaving(false)}}}>
  <div className="card-heading"><div><h3>{title}</h3>{description&&<p className="muted">{description}</p>}</div><span className="settings-scope">{scope}</span></div>
  <fieldset disabled={disabled||saving}>{children(value,patch=>{setValue(current=>({...current,...patch}));setDirty(true);setSaved(false);setError('')})}</fieldset>
  <footer className="settings-editor-footer"><span role="status">{dirty?'Есть несохранённые изменения':saved?'Изменения сохранены':'Сохранённые настройки'}</span><button className="primary" disabled={disabled||saving||!dirty}>{saving?'Сохраняем…':'Сохранить изменения'}</button></footer>
  {error&&<p className="error" role="alert">{error}</p>}
 </form>;
}
