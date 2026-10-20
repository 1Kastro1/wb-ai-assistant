/** @returns {{ratings:string[],text:string[],answers:string[],manual:boolean,brands:string[],categories:string[]}} */
export const emptyReviewFilters=()=>({ratings:[],text:[],answers:[],manual:false,brands:[],categories:[]});
export function normalizeReviewFilters(value={}){
 const unique=(items,allowed)=>[...new Set(Array.isArray(items)?items.filter(x=>typeof x==='string'&&x.length<=200&&(!allowed||allowed.includes(x))):[])].slice(0,100);
 return {ratings:unique(value?.ratings,['1','2','3','4','5']).sort(),text:unique(value?.text,['with_text','rating_only']),answers:unique(value?.answers,['answered','unanswered']),manual:value?.manual===true,brands:unique(value?.brands),categories:unique(value?.categories)};
}
export function reviewFilterCount(value){const f=normalizeReviewFilters(value);return f.ratings.length+f.text.length+f.answers.length+f.brands.length+f.categories.length+Number(f.manual)}
export function reviewFilterParams(value){const f=normalizeReviewFilters(value),p=new URLSearchParams();for(const r of f.ratings)p.append('ratings',r);for(const b of f.brands)p.append('brands',b);for(const c of f.categories)p.append('categories',c);if(f.text.length===1)p.set('text_status',f.text[0]);if(f.answers.length===1)p.set('answer_status',f.answers[0]);if(f.manual)p.set('manual_only','true');return p}
export function legacyReviewFilters(view){const f=emptyReviewFilters();if(view==='positive')f.ratings=['4','5'];if(view==='negative')f.ratings=['1','2','3'];if(['with_text','rating_only'].includes(view))f.text=[view];if(view==='manual')f.manual=true;if(view==='unanswered')f.answers=['unanswered'];return f}

