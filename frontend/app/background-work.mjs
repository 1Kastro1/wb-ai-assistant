/** One request at a time. Idle or hidden tabs should not continually poll. */
export function createBackgroundPoller({load,onValue=()=>{},onError=()=>{},isActive=()=>false,isVisible=()=>true,setTimer=setTimeout,clearTimer=clearTimeout,activeDelay=3000,idleDelay=15000,errorDelay=30000}){
 let stopped=false,running=false,timer=null;
 const schedule=delay=>{if(!stopped)timer=setTimer(tick,delay)};
 async function tick(){
  if(stopped||running)return;
  if(timer!==null){clearTimer(timer);timer=null}
  if(!isVisible()){schedule(idleDelay);return}
  running=true;let failed=false;
  try{const value=await load();if(!stopped)await onValue(value)}catch(error){failed=true;if(!stopped)onError(error)}
  finally{running=false;schedule(failed?errorDelay:isActive()?activeDelay:idleDelay)}
 }
 return {start(){schedule(isActive()?activeDelay:idleDelay)},wake(){if(!stopped&&!running&&isVisible())void tick()},stop(){stopped=true;if(timer!==null)clearTimer(timer)}};
}

/** Coalesce synchronous storage writes during scrolling; flush the final position. */
export function createScrollSaver(save,{setTimer=setTimeout,clearTimer=clearTimeout,delay=250}={}){
 let timer=null;
 const flush=()=>{if(timer!==null){clearTimer(timer);timer=null;save()}};
 return {schedule(){if(timer===null)timer=setTimer(flush,delay)},flush};
}
