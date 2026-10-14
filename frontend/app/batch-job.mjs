/** Recover an accepted selection after a disconnect without creating another job. */
export async function startSelectedDrafts({api,reviewIds,userId,storeId,storage=null,makeKey=()=>crypto.randomUUID()}){
 const ids=[...new Set(reviewIds)].sort(),key='batch-job:'+userId+':'+storeId;
 let record;try{record=JSON.parse(storage?.getItem(key)||'null')}catch{}
 if(record?.jobId&&JSON.stringify(record.ids)===JSON.stringify(ids)){
  try{const job=await api('/jobs/'+record.jobId,'GET',undefined,15000);if(['queued','running'].includes(job.status))return job}catch(e){if([401,403].includes(e?.status))throw e}
 }
 if(!record||record.jobId||JSON.stringify(record.ids)!==JSON.stringify(ids))record={ids,operationKey:makeKey()};
 const keep=()=>{try{storage?.setItem(key,JSON.stringify(record))}catch{}};keep();
 let job;
 try{job=await api('/jobs/selected-drafts','POST',{review_ids:ids,operation_key:record.operationKey},15000)}
 catch(error){if([400,401,403,404,409,422].includes(error?.status))throw error;const jobs=await api('/jobs','GET',undefined,15000);job=jobs.find(item=>item.result?.operation_key===record.operationKey&&item.result?.requested_by===userId&&item.result?.account_id===storeId);if(!job)throw new Error('Связь прервалась. Повторите запуск — уже принятое задание будет восстановлено без дублирования.')}
 if(!job?.id)throw new Error('Сервер не подтвердил подготовку выбранных отзывов. Повторите попытку.');
 record.jobId=job.id;keep();return job;
}
