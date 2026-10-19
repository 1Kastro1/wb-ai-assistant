/** One request per button; failed actions never display a success state. */
export function createButtonAction(onState){
 let running=false;
 return async action=>{
  if(running)return false;
  running=true;onState('pending');
  try{const result=await action();onState(result===false?'error':'success');return result!==false}
  catch{onState('error');return false}
  finally{running=false}
 };
}

export function savedDensity(storage,key){
 try{return storage?.getItem(key)==='compact'?'compact':'comfortable'}catch{return 'comfortable'}
}
