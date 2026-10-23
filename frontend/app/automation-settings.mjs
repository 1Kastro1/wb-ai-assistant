export function scheduleValues(schedule={}){
 return {sync_enabled:Boolean(schedule.sync_enabled??schedule.enabled),generation_enabled:Boolean(schedule.generation_enabled??(schedule.enabled&&(schedule.drafts_enabled||schedule.question_drafts_enabled))),sync_hours:schedule.sync_hours??schedule.reviews_hours??3,drafts_hours:schedule.drafts_hours??6};
}
export function schedulePayload(values){
 const sync_hours=Number(values.sync_hours),drafts_hours=Number(values.drafts_hours);
 if(![sync_hours,drafts_hours].every(value=>Number.isInteger(value)&&value>=1&&value<=168))throw new Error('Укажите целое число часов от 1 до 168.');
 return {sync_enabled:Boolean(values.sync_enabled),generation_enabled:Boolean(values.generation_enabled),sync_hours,drafts_hours};
}
