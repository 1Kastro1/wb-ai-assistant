import test from 'node:test';
import assert from 'node:assert/strict';
import config from '../next.config.mjs';

test('marketplace photos use specific CDN hosts without expanding script or connection access', async()=>{
  const headers = await config.headers();
  const policy = headers.flatMap(item=>item.headers).find(item=>item.key==='Content-Security-Policy').value;
  const images = policy.split(';').find(item=>item.trim().startsWith('img-src '));
  for(const host of ['https://*.wbbasket.ru','https://ir.ozone.ru','https://cdn1.ozone.ru','https://avatars.mds.yandex.net','https://datacamp.market.yandex.ru','https://static.uniqom.ru']) assert(images.includes(host));
  assert(!images.includes('https:;') && !images.includes('https://* '));
  assert(policy.includes("connect-src 'self';"));
  assert(policy.includes("object-src 'none';"));
});
