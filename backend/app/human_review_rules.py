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


async def repair_lamp_example(db):
    """Regenerate only the owner's known, unpublished automatic example."""
    from sqlalchemy import select
    from .db import Review,Product,Draft,now
    from .draft_audit import manually_edited
    from .review_library_audit import publication_protected
    from .services import generate_draft
    from .config import DATA
    pairs=db.execute(select(Review,Draft).join(Product,Product.id==Review.product_id)
        .join(Draft,Draft.review_id==Review.id).where(Review.is_answered==False,
            Product.name.ilike('%AB5001%'),Review.text.ilike('%нагрузку мерял%'))).all()
    eligible=[(r,d) for r,d in pairs if not manually_edited(d) and not publication_protected(db,r)]
    if len(eligible)!=1:return {'repaired':False,'reason':'Нет единственного доступного автоматического черновика'}
    review,draft=eligible[0]
    backup=Path(DATA)/'backups'/('lamp-reply-before-human-'+str(draft.revision)+'.json')
    backup.parent.mkdir(parents=True,exist_ok=True)
    if not backup.exists():
        backup.write_text(json.dumps({'review_id':review.id,'draft_id':draft.id,'text':draft.text,
            'revision':draft.revision,'quality':draft.quality,'at':now()},ensure_ascii=False),encoding='utf-8')
        backup.chmod(0o600)
    result=await generate_draft(db,review.id,instruction='Ответь тепло и по существу, используя характеристики точного артикула. Не пиши служебные отговорки.')
    return {'repaired':True,'text':result['text'],'quality_passed':result['quality'].get('passed'),
            'rules_checked':result['quality'].get('owner_rules',{}).get('checked')}


if __name__=='__main__':
    import argparse,asyncio
    from sqlalchemy import select
    from .db import Session,User
    from .owner_policy_cleanup import plan
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true')
    parser.add_argument('--repair-lamp',action='store_true');args=parser.parse_args()
    with Session() as db:
        if args.apply or args.repair_lamp:
            if not setting(db,'safe_mode',False):raise ValueError('Перед обслуживанием отправка должна быть заблокирована')
        if args.apply:
            actor=db.scalar(select(User).where(User.role=='owner',User.enabled==True).order_by(User.id))
            if not actor:raise ValueError('Нет действующего владельца')
            print(json.dumps(apply(db,actor.id),ensure_ascii=False),flush=True)
        elif not args.repair_lamp:
            changes=plan(db,policy=POLICY)
            print(json.dumps({'replacements':len(changes),'archive':sum(len(rows) for _,rows in changes)},ensure_ascii=False),flush=True)
        if args.repair_lamp:
            print(json.dumps(asyncio.run(repair_lamp_example(db)),ensure_ascii=False),flush=True)
