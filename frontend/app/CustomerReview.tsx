type Review = {text?:string;sections?:Record<string,string>};
export function hasCustomerText(review:Review){
 return Boolean(review.text?.trim()||Object.values(review.sections||{}).some(value=>String(value||'').trim()));
}
export default function CustomerReview({review}:{review:Review}){
 const sections=review.sections||{};
 const structured=['advantages','disadvantages','comment'].some(key=>Object.prototype.hasOwnProperty.call(sections,key));
 if(!hasCustomerText(review))return null;
 return <section className="customer-review" aria-label="Отзыв покупателя"><small>ОТЗЫВ ПОКУПАТЕЛЯ</small>{structured?<dl className="customer-review-sections">{[['advantages','Достоинства'],['disadvantages','Недостатки'],['comment','Комментарий']].map(([key,label])=><div key={key} className={'customer-review-section '+key}><dt>{label}</dt><dd className={'review-text '+(!sections[key]?.trim()?'empty':'')}>{sections[key]?.trim()||'Не указаны'}</dd></div>)}</dl>:<><p className="review-text">{review.text}</p><small className="review-source-note">Маркетплейс передал отзыв общим текстом.</small></>}</section>;
}
