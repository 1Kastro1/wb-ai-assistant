const paths={'/settings/wb-token':'Обновил подключение Wildberries','/settings/wb-test':'Проверил подключение Wildberries','/settings/assistant':'Изменил состояние ассистента','/auto-replies/start':'Запустил автоответы','/auto-replies/pause':'Приостановил автоответы','/jobs/drafts':'Запустил подготовку ответов','/jobs/single-draft':'Запустил подготовку нового ответа','/jobs/question-drafts':'Запустил подготовку ответов на вопросы','/actions/publish':'Подготовил ответы к публикации','/settings/schedule':'Изменил расписание','/settings/search':'Изменил поиск инструкций','/settings/performance':'Изменил режим генерации','/memory/propose':'Предложил новое правило','/portable/sync-now':'Создал резервную копию','/settings/portable-sync':'Изменил резервное копирование','/auth/logout':'Вышел из профиля','/auth/password':'Изменил пароль','/stores':'Добавил магазин','/chat':'Работал с ассистентом'};
const events={rule_proposed:'Предложил новое правило',rule_updated:'Изменил предложенное правило',rule_added:'Добавил правило в память',rule_deleted:'Удалил правило из памяти',rule_rejected:'Отклонил предложенное правило'};
/** @param {any} item */
export function activityDescription(item){
 const path=item.path||'',event=item.details?.event;
 if(events[event])return events[event];
 if(paths[path])return paths[path];
 if(path.startsWith('/jobs/sync/'))return 'Запустил синхронизацию '+(path.endsWith('reviews')?'отзывов':path.endsWith('questions')?'вопросов':'товаров');
 if(path.startsWith('/memory/')&&item.method==='DELETE')return 'Удалил правило из памяти';
 if(path.startsWith('/actions/')&&path.endsWith('/memory'))return 'Изменил предложенное правило';
 if(path.startsWith('/actions/')&&path.endsWith('/confirm'))return 'Подтвердил операцию';
 if(path.startsWith('/actions/')&&path.endsWith('/cancel'))return 'Отменил операцию';
 if(path.startsWith('/actions/')&&item.method==='DELETE')return 'Удалил операцию';
 if(path.startsWith('/drafts/')&&path.endsWith('/feedback'))return 'Оценил подготовленный ответ';
 if(path.startsWith('/drafts/')&&item.method==='PATCH')return 'Отредактировал ответ покупателю';
 if(path.startsWith('/products/')&&path.includes('/facts'))return item.method==='DELETE'?'Удалил факт о товаре':path.endsWith('/verify')?'Подтвердил источник информации о товаре':'Добавил сведения о товаре';
 if(path.startsWith('/users/'))return 'Изменил учётную запись сотрудника';
 if(path==='/users')return 'Добавил сотрудника';
 if(path.startsWith('/stores/')&&path.endsWith('/activate'))return 'Выбрал магазин';
 if(path.startsWith('/settings/'))return 'Изменил настройки магазина';
 if(path.startsWith('/jobs/'))return path.endsWith('/retry')?'Повторно запустил обработку':path.endsWith('/cancel')?'Остановил обработку':'Запустил обработку';
 return 'Выполнил изменение';
}
