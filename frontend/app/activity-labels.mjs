const paths={'/settings/wb-token':'Обновлено подключение Wildberries','/settings/wb-test':'Проверено подключение Wildberries','/settings/assistant':'Изменено состояние ассистента','/auto-replies/start':'Автоответы запущены','/auto-replies/pause':'Автоответы приостановлены','/jobs/drafts':'Начата подготовка ответов','/jobs/single-draft':'Начата подготовка нового ответа','/jobs/question-drafts':'Начата подготовка ответов на вопросы','/actions/publish':'Ответы подготовлены к публикации','/settings/schedule':'Расписание изменено','/settings/search':'Изменён поиск инструкций','/settings/performance':'Изменён режим генерации','/memory/propose':'Предложено новое правило','/portable/sync-now':'Создана резервная копия','/settings/portable-sync':'Изменено резервное копирование','/auth/logout':'Выход из профиля','/auth/password':'Пароль изменён','/stores':'Добавлен магазин','/chat':'Работа с ассистентом'};
const events={registration_accepted:'Заявка на регистрацию принята',registration_rejected:'Заявка на регистрацию отклонена',user_deleted:'Пользователь удалён',rule_proposed:'Предложено новое правило',rule_updated:'Предложенное правило изменено',rule_added:'Правило добавлено в память',rule_deleted:'Правило удалено из памяти',rule_rejected:'Предложенное правило отклонено'};
/** @param {any} item */
export function activityDescription(item){
 const path=item.path||'',event=item.details?.event;
 if(events[event])return events[event];
 if(paths[path])return paths[path];
 if(path.startsWith('/jobs/sync/'))return 'Начата синхронизация '+(path.endsWith('reviews')?'отзывов':path.endsWith('questions')?'вопросов':'товаров');
 if(path.startsWith('/memory/')&&item.method==='DELETE')return 'Правило удалено из памяти';
 if(path.startsWith('/actions/')&&path.endsWith('/memory'))return 'Предложенное правило изменено';
 if(path.startsWith('/actions/')&&path.endsWith('/confirm'))return 'Операция подтверждена';
 if(path.startsWith('/actions/')&&path.endsWith('/cancel'))return 'Операция отменена';
 if(path.startsWith('/actions/')&&item.method==='DELETE')return 'Операция удалена';
 if(path.startsWith('/drafts/')&&path.endsWith('/feedback'))return 'Оценён подготовленный ответ';
 if(path.startsWith('/drafts/')&&item.method==='PATCH')return 'Ответ покупателю отредактирован';
 if(path.startsWith('/products/')&&path.includes('/facts'))return item.method==='DELETE'?'Удалён факт о товаре':path.endsWith('/verify')?'Подтверждён источник информации о товаре':'Добавлены сведения о товаре';
 if(path.startsWith('/users/'))return 'Изменена учётная запись сотрудника';
 if(path==='/users')return 'Добавлен сотрудник';
 if(path.startsWith('/stores/')&&path.endsWith('/activate'))return 'Выбран магазин';
 if(path.startsWith('/settings/'))return 'Настройки магазина изменены';
 if(path.startsWith('/jobs/'))return path.endsWith('/retry')?'Обработка запущена повторно':path.endsWith('/cancel')?'Обработка остановлена':'Обработка запущена';
 return 'Изменение в приложении';
}
