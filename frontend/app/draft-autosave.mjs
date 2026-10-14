/** Serializes saves and preserves newer typing, conflict copies and recoverable local edits. */
export function createDraftAutosaver({draft,save,onState=()=>{},storage=null,key='',delay=1200,setTimer=setTimeout,clearTimer=clearTimeout}){
 let text=draft.text,savedText=draft.text,revision=draft.revision,status='saved',error='',timer=null,inFlight=null,previous=null,disposed=false,remote=null;
 const emit=()=>onState({text,savedText,revision,status,error,canUndo:text!==savedText||previous!==null,remote});
 const keep=()=>{try{if(text===savedText)storage?.removeItem(key);else storage?.setItem(key,JSON.stringify({text,revision,savedText}))}catch{}};
 const stop=()=>{if(timer!==null)clearTimer(timer);timer=null};
 function schedule(){stop();if(text===savedText){status='saved';error='';keep();emit();return}status='unsaved';error='';keep();emit();timer=setTimer(()=>{timer=null;void flush().catch(()=>{})},delay)}
 async function flush(){
  stop();if(inFlight)return inFlight.then(()=>text!==savedText&&status==='unsaved'?flush():undefined);
  if(status==='conflict')throw new Error(error);if(text===savedText)return;
  if(text.trim().length<2||text.length>5000){status='error';error='Ответ должен содержать от 2 до 5000 символов';emit();throw new Error(error)}
  const submitted=text,expected=revision;status='saving';error='';emit();
  const task=(async()=>{try{const result=await save(submitted,expected);if(!result||typeof result.text!=='string'||!(result.revision>expected))throw new Error('Сохранение не подтверждено сервером');previous=savedText;savedText=result.text;revision=result.revision;if(text===submitted)text=result.text;status=text===savedText?'saved':'unsaved';error='';remote=null;keep();emit()}catch(e){status=e?.status===409||/черновик.*измен|ревизи/i.test(e?.message||'')?'conflict':'error';error=status==='conflict'?'Ответ изменён другим пользователем. Ваш текст сохранён в этом браузере.':e?.message||'Не удалось сохранить. Ваш текст сохранён в этом браузере.';keep();emit();throw e}finally{inFlight=null;if(status==='unsaved'&&!disposed)schedule()}})();
  inFlight=task;return task;
 }
 function observe(value){
  if(value.revision<=revision)return;
  if(inFlight)return;
  if(text!==savedText){stop();remote=value;status='conflict';error='Есть новая версия ответа. Сверьте её со своим текстом.';keep();emit();return}
  savedText=text=value.text;revision=value.revision;previous=null;status='saved';error='';keep();emit();
 }
 const controller={change(value){text=value;if(status==='conflict'){keep();emit()}else schedule()},flush,observe,undo(){stop();text=text!==savedText?savedText:(previous??savedText);previous=null;schedule()},resolve(value,keepMine){stop();revision=value.revision;savedText=value.text;remote=null;previous=null;if(!keepMine)text=value.text;status=text===savedText?'saved':'unsaved';error='';keep();emit();if(keepMine)schedule()},connect(options){save=options.save;onState=options.onState;disposed=false;observe(options.draft);emit();if(status==='unsaved'&&!inFlight)schedule()},canRelease(){return disposed&&!inFlight&&status==='saved'},dispose(){stop();if(status==='unsaved')void flush().catch(()=>{});disposed=true},snapshot(){return {text,savedText,revision,status,error,remote}}};
 try{const local=JSON.parse(storage?.getItem(key)||'null');if(local&&typeof local.text==='string'&&local.text!==draft.text){text=local.text;if(local.revision===revision){schedule()}else{remote=draft;status='conflict';error='Ваши несохранённые правки восстановлены. На сервере уже есть новая версия.';emit()}}}catch{}
 keep();emit();return controller;
}

const sharedSavers=new Map();
/** Reopening a row while its save is in flight must use the same serial queue. */
export function acquireDraftAutosaver(options){
 const existing=sharedSavers.get(options.key);
 if(existing){existing.connect(options);return existing}
 const saver=createDraftAutosaver(options);sharedSavers.set(options.key,saver);
 if(sharedSavers.size>100)for(const [key,value] of sharedSavers){if(sharedSavers.size<=100)break;if(value!==saver&&value.canRelease())sharedSavers.delete(key)}
 return saver;
}
