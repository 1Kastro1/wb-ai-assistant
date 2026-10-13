/** Resume the same server job after a disconnect. Never blindly retry a POST.
 * @param {any} options
 */
export async function waitForDraftJob(options) {
  const {api,reviewId,instruction='',storage=null,now=()=>Date.now(),delay=ms=>new Promise(r=>setTimeout(r,ms)),timeoutMs=180000}=options;
  const key='draft-job:'+reviewId,deadline=now()+timeoutMs;
  const read=()=>{try{return storage?.getItem(key)}catch{return null}};
  const keep=id=>{try{storage?.setItem(key,id)}catch{}};
  const clear=()=>{try{storage?.removeItem(key)}catch{}};
  let job;
  const saved=read();
  if(saved){
    try{job=await api('/jobs/'+saved,'GET',undefined,15000)}
    catch(e){if([401,403,404].includes(e?.status)){clear();throw e}else throw new Error('Связь с сервером прервалась. Повторите ожидание — задание сохранено.')}
  }else{
    try{job=await api('/jobs/single-draft','POST',{review_id:reviewId,instruction},15000)}
    catch(e){
      if([400,401,403,404,422].includes(e?.status))throw e;
      // The server may have accepted the request before the connection broke.
      try{const jobs=await api('/jobs','GET',undefined,15000);job=jobs.find(j=>(['queued','running'].includes(j.status)||(j.status==='completed'&&Date.parse(j.created_at)>=deadline-timeoutMs-5000))&&j.result?.selected_ids?.includes(reviewId))}catch{}
      if(!job)throw new Error('Не удалось связаться с сервером. Повторите попытку; текущее задание будет проверено перед запуском.');
    }
  }
  if(!job?.id)throw new Error('Ответ сервера не получен. Повторите попытку.');
  keep(job.id);
  while(now()<deadline){
    if(job.status==='completed'){
      if(!job.result?.draft){clear();throw new Error('Черновик не получен. Обновите список отзывов.')}
      let draft=job.result.draft;
      try{const latest=await api('/drafts/'+encodeURIComponent(draft.id),'GET',undefined,15000);if(latest?.id===draft.id&&typeof latest.text==='string'&&latest.revision>=draft.revision)draft=latest}catch{}
      clear();return draft;
    }
    if(['failed','interrupted','cancelled'].includes(job.status)){
      clear();throw new Error(job.status==='cancelled'?'Подготовка отменена. Можно запустить её снова.':'Не удалось завершить подготовку. Повторите попытку.');
    }
    await delay(2000);
    try{job=await api('/jobs/'+job.id,'GET',undefined,15000)}
    catch(e){if([401,403,404].includes(e?.status)){clear();throw e}}
  }
  throw new Error('Ответ ещё готовится на сервере. Нажмите «Продолжить ожидание», чтобы получить результат того же задания.');
}
