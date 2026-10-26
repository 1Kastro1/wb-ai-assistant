export const topicLabels={'упаков':'Упаковка','достав':'Доставка','запах':'Аромат и запах','скрип':'Скрип и шум','вибрац':'Вибрации','тормоз':'Работа тормозов','подбор':'Подбор товара','совместим':'Совместимость','другое':'Без определённой темы'};
export function draftState(item){
 if(item.is_answered)return 'Опубликован';
 if(!item.has_draft)return 'Ответ ещё не создан';
 if(item.manual)return 'Нужна личная проверка';
 if(item.verification_passed&&item.status==='draft_ready')return 'Проверен · ожидает подтверждения';
 return 'Черновик сохранён · нужна проверка';
}
export function notificationGroups(items){
 const groups=new Map();
 for(const item of items){
  const key=[item.store_id||'',item.kind,item.severity,item.user_id||'',item.kind==='job_failed'?(item.message||''):''].join(':');
  const group=groups.get(key);
  if(group){group.count++;group.unread+=Number(!item.read);group.events.push(item);if(item.created_at>group.latest.created_at)group.latest=item;}
  else groups.set(key,{key,count:1,unread:Number(!item.read),latest:item,events:[item]});
 }
 return [...groups.values()].sort((a,b)=>b.latest.created_at.localeCompare(a.latest.created_at));
}
