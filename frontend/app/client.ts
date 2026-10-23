const API='/api';
let csrf='';
const snapshots=new Map<string,{etag:string,value:any}>();

export function setCsrf(value:string){csrf=value;snapshots.clear()}

export async function api(path:string,method='GET',body?:unknown,timeoutMs=180000){
  const multipart=body instanceof FormData;
  const controller=new AbortController();
  const timer=setTimeout(()=>controller.abort(),timeoutMs);
  if(method!=='GET')snapshots.clear();
  const snapshot=method==='GET'&&path==='/jobs?compact=true'?snapshots.get(path):undefined;
  try{
    const response=await fetch(API+path,{method,credentials:'include',signal:controller.signal,headers:{...(multipart?{}:{'Content-Type':'application/json'}),'X-CSRF-Token':csrf,...(snapshot?{'If-None-Match':snapshot.etag}:{})},body:body===undefined?undefined:multipart?body:JSON.stringify(body)});
    if(response.status===304&&snapshot)return snapshot.value;
    const raw=await response.text();let result:any={};
    try{result=raw?JSON.parse(raw):{}}catch{throw new Error(response.ok?'Сервер вернул некорректный ответ':'Сервер временно не ответил. Повторите попытку.')}
    if(!response.ok){const error:any=new Error(result.detail||'Не удалось выполнить запрос');error.status=response.status;throw error;}
    const etag=response.headers.get('etag');if(method==='GET'&&path==='/jobs?compact=true'&&etag)snapshots.set(path,{etag,value:result});return result;
  }catch(error){
    if(controller.signal.aborted)throw new Error('Сервер не ответил вовремя. Проверьте соединение. Если задание уже запущено, его состояние сохранено; вернитесь к карточке и продолжите ожидание.');
    throw error;
  }finally{
    clearTimeout(timer);
  }
}

export async function apiAudio(path:string,body:unknown){
  const response=await fetch(API+path,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});
  if(!response.ok){const result=await response.json().catch(()=>({}));throw new Error(result.detail||'Не удалось озвучить ответ')}return response.blob();
}

export async function apiDownload(path:string,body:unknown){
  const response=await fetch(API+path,{method:'POST',credentials:'include',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});
  if(!response.ok){const result=await response.json().catch(()=>({}));throw new Error(result.detail||'Не удалось создать переносимую копию')}
  const disposition=response.headers.get('content-disposition')||'';const filename=disposition.match(/filename="?([^";]+)"?/)?.[1]||'wb-assistant-backup.wbai';return {blob:await response.blob(),filename};
}
