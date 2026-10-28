import {test} from 'node:test';
import assert from 'node:assert/strict';
import {emptyReviewFilters,normalizeReviewFilters,reviewFilterParams} from '../app/review-filters.mjs';
import {readViewPreferences} from '../app/workspace-data.mjs';

test('generated reply filter is independent, persisted and treats both choices as all',()=>{
 const f={...emptyReviewFilters(),drafts:['generated'],answers:['unanswered']};
 assert.equal(reviewFilterParams(f).get('draft_status'),'generated');
 assert.equal(reviewFilterParams(f).get('answer_status'),'unanswered');
 assert.equal(reviewFilterParams({...f,drafts:['missing']}).get('draft_status'),'missing');
 assert.equal(reviewFilterParams({...f,drafts:['generated','missing']}).has('draft_status'),false);
 assert.deepEqual(readViewPreferences({getItem:()=>JSON.stringify({reviewFilters:f})},'store-a').reviewFilters,f);
 assert.deepEqual(normalizeReviewFilters({drafts:['generated','generated','bad']}).drafts,['generated']);
});
test('text and multiple ratings travel as independent server conditions',()=>{const f={...emptyReviewFilters(),ratings:['1','2','3'],text:['with_text'],brands:['A, B','EIKOSHA']};const p=reviewFilterParams(f);assert.deepEqual(p.getAll('ratings'),['1','2','3']);assert.equal(p.get('text_status'),'with_text');assert.deepEqual(p.getAll('brands'),['A, B','EIKOSHA']);assert.equal(reviewFilterParams(emptyReviewFilters()).toString(),'')});
test('both text or answer options mean either, ratings remain constrained',()=>{const p=reviewFilterParams({ratings:['2'],text:['with_text','rating_only'],answers:['answered','unanswered']});assert.equal(p.has('text_status'),false);assert.equal(p.has('answer_status'),false);assert.deepEqual(p.getAll('ratings'),['2'])});
test('saved combined filters survive restore and reject malformed values',()=>{const filters={...emptyReviewFilters(),ratings:['2'],text:['with_text'],manual:true};assert.deepEqual(readViewPreferences({getItem:()=>JSON.stringify({reviewFilters:filters})},'store-a').reviewFilters,filters);assert.deepEqual(normalizeReviewFilters({ratings:['0','2','2',null],brands:'bad',manual:'true'}),{...emptyReviewFilters(),ratings:['2']})});
