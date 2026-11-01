export function scheduleValues(schedule={}){
 const active=Boolean(schedule.enabled);
 return {
  reviews_sync_enabled:active&&Boolean(schedule.reviews_sync_enabled??schedule.sync_enabled??true),
  questions_sync_enabled:active&&Boolean(schedule.questions_sync_enabled??schedule.sync_enabled??true),
  drafts_enabled:active&&Boolean(schedule.drafts_enabled),
  question_drafts_enabled:active&&Boolean(schedule.question_drafts_enabled),
  publication_enabled:active&&Boolean(schedule.publication_enabled),
  publication_hours:schedule.publication_hours??1,
  publication_reviews:schedule.publication_reviews??true,
  publication_questions:schedule.publication_questions??true,
  reviews_hours:schedule.reviews_hours??schedule.sync_hours??3,
  questions_hours:schedule.questions_hours??schedule.sync_hours??3,
  drafts_hours:schedule.drafts_hours??6,
  question_drafts_hours:schedule.question_drafts_hours??schedule.drafts_hours??6,
 };
}
export function schedulePayload(values){
 const payload={};
 for(const key of ['reviews_hours','questions_hours','drafts_hours','question_drafts_hours','publication_hours']){
  const value=Number(values[key]);
  if(!Number.isInteger(value)||value<1||value>168)throw new Error('Укажите целое число часов от 1 до 168.');
  payload[key]=value;
 }
 for(const key of ['reviews_sync_enabled','questions_sync_enabled','drafts_enabled','question_drafts_enabled','publication_enabled','publication_reviews','publication_questions'])payload[key]=Boolean(values[key]);
 if(payload.publication_enabled&&!payload.publication_reviews&&!payload.publication_questions)throw new Error('Выберите ответы на отзывы или вопросы для автоматической выгрузки.');
 return payload;
}
