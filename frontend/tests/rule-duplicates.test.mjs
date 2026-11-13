import test from 'node:test';
import assert from 'node:assert/strict';
import {duplicateRule} from '../app/rule-duplicates.mjs';
const row={id:'a',text:'Не обещай результат.',kind:'REVIEW_RULE',scope:'brand:EIKOSHA',enabled:false};
test('rules detect whitespace and case copies including disabled rules',()=>{
 assert.equal(duplicateRule([row],'  НЕ обещай\n результат.  ','REVIEW_RULE','brand: eikosha ').id,'a');
});
test('rules preserve audience, scope, negation, numbers and punctuation',()=>{
 for(const [text,kind,scope] of [['Не обещай результат.','QUESTION_RULE',row.scope],['Не обещай результат.','REVIEW_RULE','brand:OTHER'],['Обещай результат.','REVIEW_RULE',row.scope],['Не обещай результат!','REVIEW_RULE',row.scope]])assert.equal(duplicateRule([row],text,kind,scope),undefined);
 assert.equal(duplicateRule([{...row,text:'Ждать 5 минут.'}],'Ждать 15 минут.',row.kind,row.scope),undefined);
 assert.equal(duplicateRule([{...row,duplicate_of:'canonical'}],row.text,row.kind,row.scope),undefined);
});
