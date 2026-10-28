/** @returns {{ratings:string[],text:string[],answers:string[],drafts:string[],manual:boolean,technical:boolean,product:null|{id:string,name:string,days:number},brands:string[],categories:string[]}} */
export const emptyReviewFilters=()=>({ratings:[],text:[],answers:[],drafts:[],manual:false,technical:false,product:null,brands:[],categories:[]});
export function normalizeReviewFilters(value={}){
 const unique=(items,allowed)=>[...new Set(Array.isArray(items)?items.filter(x=>typeof x==='string'&&x.length<=200&&(!allowed||allowed.includes(x))):[])].slice(0,100);
 return {ratings:unique(value?.ratings,['1','2','3','4','5']).sort(),text:unique(value?.text,['with_text','rating_only']),answers:unique(value?.answers,['answered','unanswered']),drafts:unique(value?.drafts,['generated','missing']),manual:value?.manual===true,technical:value?.technical===true,product:typeof value?.product?.id==='string'&&value.product.id.length>0&&value.product.id.length<=200?{id:value.product.id,name:String(value.product.name||'Выбранный товар').slice(0,2000),days:Number.isInteger(value.product.days)&&value.product.days>=0&&value.product.days<=3650?value.product.days:0}:null,brands:unique(value?.brands),categories:unique(value?.categories)};
}
export function reviewFilterCount(value){const f=normalizeReviewFilters(value);return f.ratings.length+f.text.length+f.answers.length+f.drafts.length+f.brands.length+f.categories.length+Number(f.manual)+Number(f.technical)+Number(Boolean(f.product))}
export function reviewFilterParams(value){const f=normalizeReviewFilters(value),p=new URLSearchParams();for(const r of f.ratings)p.append('ratings',r);for(const b of f.brands)p.append('brands',b);for(const c of f.categories)p.append('categories',c);if(f.text.length===1)p.set('text_status',f.text[0]);if(f.answers.length===1)p.set('answer_status',f.answers[0]);if(f.drafts.length===1)p.set('draft_status',f.drafts[0]);if(f.manual)p.set('manual_only','true');if(f.technical)p.set('technical_only','true');if(f.product){p.set('product_id',f.product.id);if(f.product.days)p.set('days',String(f.product.days));}return p}
export function legacyReviewFilters(view){const f=emptyReviewFilters();if(view==='positive')f.ratings=['4','5'];if(view==='negative')f.ratings=['1','2','3'];if(['with_text','rating_only'].includes(view))f.text=[view];if(view==='manual')f.manual=true;if(view==='unanswered')f.answers=['unanswered'];return f}


// Low ratings stay in the ordinary review-and-confirm flow, never the positive batch.
export const lowRatingReviewFilters=()=>({...emptyReviewFilters(),ratings:['1','2','3'],answers:['unanswered'],drafts:['generated']});

// An empty selection means all reviews. Never restore a removed answer filter.
export function reviewAnswerFilters(value,answer){
 const filters=normalizeReviewFilters(value);
 return {...filters,answers:answer==='all'?[]:['unanswered','answered'].includes(answer)?[answer]:filters.answers.length===1?filters.answers:[]};
}

export const negativeProductFilters=(product,days=0)=>normalizeReviewFilters({...emptyReviewFilters(),ratings:['1','2','3'],product:{id:product.id,name:product.name,days}});
