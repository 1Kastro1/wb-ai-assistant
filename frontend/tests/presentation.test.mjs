
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {publicationModeText,scopeLabel,ruleTitle,permissionLabel,notificationReadState} from '../app/presentation.mjs';
test('publication copy follows actual mode and never assumes a working mode from missing data',()=>{
 assert.match(publicationModeText({test_mode:false,safe_mode:false}),/Рабочий режим/);
 assert.match(publicationModeText({test_mode:true,safe_mode:false}),/Тестовый режим/);
 assert.match(publicationModeText({test_mode:false,safe_mode:true}),/заблокирована/);
 assert.match(publicationModeText({}),/Проверяем/);
});
test('scope labels retain colons inside target names and rule titles derive from their text',()=>{
 assert.equal(scopeLabel('brand:ABC:DEF'),'Бренд · ABC:DEF');
 assert.equal(scopeLabel('global'),'Все товары');
 assert.equal(ruleTitle('Не проси причину повторно. Покупатель её описал.'),'Не проси причину повторно.');
 assert(ruleTitle('Очень длинное правило '.repeat(20)).length<=90);
 assert.equal(permissionLabel('wb:operate'),'Работа с Wildberries');
});
test('read notifications are never presented as resolved',()=>{
 assert.equal(notificationReadState({unread:0}),'Прочитано');
 assert.equal(notificationReadState({unread:2}),'Есть непрочитанные');
});
