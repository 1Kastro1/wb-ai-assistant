'use client';
import {useState} from 'react';

export default function ProductThumbnail({product}:{product:Record<string,any>}){
  const url=typeof product.image_url==='string'&&/^https:\/\//i.test(product.image_url)?product.image_url:'';
  const [failed,setFailed]=useState('');
  return <span className="product-thumbnail" aria-label={url&&failed!==url?undefined:'Фото товара отсутствует'}>
    {url&&failed!==url?<img src={url} alt={product.name||'Товар'} width="56" height="56" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={()=>setFailed(url)}/>:<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z"/><path d="m4 7.5 8 4.5 8-4.5M12 12v9M8 5.25l8 4.5"/></svg>}
  </span>;
}
