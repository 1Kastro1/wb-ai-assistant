"""Owner-authorized, reversible update of the three reviewed whole rules."""
import json
from pathlib import Path
from .db import now,setting,set_setting

POLICY=json.loads(Path(__file__).with_name('human_review_policy.json').read_text(encoding='utf-8'))


def apply(db,actor_id,backup_dir=None):
    """Run with the queue consumer stopped; never rewrite customer answers."""
    key='applied:'+POLICY['version']
    if setting(db,key,None):return {'already_applied':True}
    from .owner_policy_cleanup import apply as cleanup,replacement_id
    from .rule_change_recheck import rebase_library_audits,snapshot
    from .db import Memory
    result=cleanup(db,actor_id,backup_dir,policy=POLICY,enqueue_checks=False)
    scopes=[snapshot(db.get(Memory,replacement_id(change))) for change in POLICY['changes']]
    rebase_library_audits(db,scopes)
    set_setting(db,'auto_case_rules_enabled',True)
    set_setting(db,key,{'at':now(),'actor_id':actor_id,'result':result})
    db.commit()
    return {**result,'auto_case_rules_enabled':True}
