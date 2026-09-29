const API='/api';
let csrf='';

export function setCsrf(value:string){csrf=value}

export async function api(path:string,method='GET',body?:unknown){
  const multipart=body instanceof FormData;
  const response=await fetch(API+path,{method,credentials:'include',headers:{...(multipart?{}:{'Content-Type':'application/json'}),'X-CSRF-Token':csrf},body:body===undefined?undefined:multipart?body:JSON.stringify(body)});
  const raw=await response.text();let result:any={};
  try{result=raw?JSON.parse(raw):{}}catch{result={detail:response.ok?'Сервер вернул некорректный ответ':'Сервер временно не ответил. Повторите попытку.'}}
  if(!response.ok)throw new Error(result.detail||'Не удалось выполнить запрос');return result;
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
