import {test} from 'node:test';
import assert from 'node:assert/strict';
import {restoredWorkspacePage,readViewPreferences} from '../app/workspace-data.mjs';
const pages=['Главная','Ассистент','Очередь ответов','Отзывы','Вопросы','Товары','История действий','Уведомления','Правила ответов','Настройки'];

test('switching stores keeps every current section despite a different saved section',()=>{
 for(const current of pages){
  const saved=current==='Отзывы'?'Товары':'Отзывы';
  assert.equal(restoredWorkspacePage('workspace-view:user:wb',current,saved,pages),current);
  assert.equal(restoredWorkspacePage('workspace-view:user:ozon',current,saved,pages),current);
 }
});
test('a fresh page still restores its last section and rejects removed sections',()=>{
 assert.equal(restoredWorkspacePage('','Ассистент','Вопросы',pages),'Вопросы');
 assert.equal(restoredWorkspacePage('','Ассистент','old-section',pages),'Ассистент');
});
test('store-specific filters remain available without changing the current section',()=>{
 const saved=readViewPreferences({getItem:()=>JSON.stringify({page:'Отзывы',productQuery:'4ND-1008W',reviewFilters:{ratings:[1,2],text:['with_text']}})},'store');
 assert.equal(saved.productQuery,'4ND-1008W');
 assert.equal(restoredWorkspacePage('workspace-view:user:wb','Товары',saved.page,pages),'Товары');
});
