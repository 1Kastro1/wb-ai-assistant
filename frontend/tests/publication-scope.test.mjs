import {test} from 'node:test';
import assert from 'node:assert/strict';
import {publicationDestination} from '../app/publication-scope.mjs';

test('confirmation labels identify the selected marketplace without falling back to WB',()=>{
 assert.equal(publicationDestination('wb').name,'Wildberries');
 assert.equal(publicationDestination('ozon').name,'Ozon');
 assert.equal(publicationDestination('yandex').name,'Яндекс Маркет');
 assert.equal(publicationDestination('unknown').name,'Маркетплейс');
});

