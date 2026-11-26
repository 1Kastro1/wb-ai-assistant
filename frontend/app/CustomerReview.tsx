type Review = {text?:string;sections?:Record<string,string>};
const REVIEW_FIELDS=[['advantages','Достоинства'],['disadvantages','Недостатки'],['comment','Комментарий']];
const SUPPLEMENT=['supplement','Дополнение покупателя'];
export function hasCustomerText(review:Review){
 return Boolean(review.text?.trim()||[...REVIEW_FIELDS,SUPPLEMENT].some(([key])=>review.sections?.[key]?.trim()));
}
export default function CustomerReview({review}:{review:Review}){
 const sections=review.sections||{};
 const structured=['advantages','disadvantages','comment'].some(key=>Object.prototype.hasOwnProperty.call(sections,key));
 if(!hasCustomerText(review))return null;
 const fields=sections.supplement?.trim()?[...REVIEW_FIELDS,SUPPLEMENT]:REVIEW_FIELDS;
 return <section className="customer-review" aria-label="Отзыв покупателя"><small>ОТЗЫВ ПОКУПАТЕЛЯ</small>{structured?<dl className="customer-review-sections">{fields.map(([key,label])=><div key={key} className={'customer-review-section '+key}><dt>{label}</dt><dd className={'review-text '+(!sections[key]?.trim()?'empty':'')}>{sections[key]?.trim()||'Не указаны'}</dd></div>)}</dl>:<><p className="review-text">{review.text}</p><small className="review-source-note">Маркетплейс передал отзыв общим текстом.</small></>}</section>;
}
