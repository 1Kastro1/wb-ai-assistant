
/** Labels only: never infer publication permission or change stored scopes. */
export function publicationModeText(security={}) {
 if(security.safe_mode===true)return 'Отправка заблокирована. Подготовленные ответы сохраняются в приложении.';
 if(security.test_mode===true)return 'Тестовый режим: ответы не отправляются в маркетплейсы.';
 if(security.test_mode===false&&security.safe_mode===false)return 'Рабочий режим: отправка ответов — только после Вашего подтверждения.';
 return 'Проверяем режим публикации. Отправка требует Вашего подтверждения.';
}
export const permissionNames={'*':'Все разрешения','data:read':'Просмотр данных','assistant:use':'Работа с ассистентом','wb:operate':'Работа с Wildberries','ozon:operate':'Работа с Ozon','yandex:operate':'Работа с Яндекс Маркетом'};
export function permissionLabel(value){return permissionNames[value]||'Дополнительное разрешение';}
export function scopeLabel(value='global'){
 if(value==='global')return 'Все товары';
 const [kind,...name]=String(value).split(':');
 return ({brand:'Бренд',category:'Категория',product:'Товар'}[kind]||'Область')+' · '+name.join(':');
}
export function ruleTitle(text='') {const line=String(text).trim().split(/\n|(?<=[.!?])\s+/)[0]||'Правило ответа';return line.length>90?line.slice(0,87)+'…':line;}
export function notificationReadState(group){return group.unread>0?'Есть непрочитанные':'Прочитано';}
