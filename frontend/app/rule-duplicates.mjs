export const normalizedRuleText=value=>String(value||'').normalize('NFC').trim().replace(/\s+/gu,' ').toLowerCase();
export function normalizedRuleScope(value){const [kind,...rest]=String(value||'global').split(':');return kind.trim().toLowerCase()+(rest.length?':'+rest.join(':').trim().toLowerCase():'')}
export function duplicateRule(items,text,kind,scope){
 const key=normalizedRuleText(text);
 const found=key?items.find(item=>(!item.duplicate_of||item.duplicate_reason==='covered')&&item.kind===kind&&normalizedRuleScope(item.scope)===normalizedRuleScope(scope)&&normalizedRuleText(item.text)===key):undefined;
 return found?.duplicate_of?items.find(item=>item.id===found.duplicate_of&&item.kind===kind):found;
}
