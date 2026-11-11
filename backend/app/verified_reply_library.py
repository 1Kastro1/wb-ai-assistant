"""One reply body, independent approvals; similar examples never confer approval."""
from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import re
import time
import unicodedata
from sqlalchemy import select, delete, func
from .db import VerifiedReplyText,VerifiedReplyContext,Setting,IS_SQLITE


def text_id(answer):
    normalized=re.sub(r'\s+',' ',unicodedata.normalize('NFKC',answer)).strip().casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def insert():
    from sqlalchemy.dialects.sqlite import insert as sqlite
    from sqlalchemy.dialects.postgresql import insert as postgres
    return sqlite if IS_SQLITE else postgres


def body(db,answer):
    tid=text_id(answer)
    db.execute(insert()(VerifiedReplyText).values(id=tid,text=answer)
        .on_conflict_do_nothing(index_elements=['id']))
    saved=db.get(VerifiedReplyText,tid)
    # Keep exact approved presentation without duplicating the reply body.
    patches=[[a,b,answer[c:d]] for tag,a,b,c,d in SequenceMatcher(None,saved.text,answer,autojunk=False).get_opcodes() if tag!='equal']
    return tid,patches


def render(answer,patches):
    for start,end,value in reversed(patches or []):
        if not 0<=start<=end<=len(answer):raise ValueError('Invalid template presentation')
        answer=answer[:start]+value+answer[end:]
    return answer


def remember(db,key,kind,answer,verdict,source='',marketplace='',verified_at=None):
    tid,patches=body(db,answer)
    cid=hashlib.sha256((key+':'+kind+':'+tid).encode()).hexdigest()
    values={'template_id':tid,'context_key':key,'kind':kind,'verdict':deepcopy(verdict),
        'presentation':patches,'source_review_id':source,'source_marketplace':marketplace,
        'verified_at':time.time() if verified_at is None else verified_at}
    db.execute(insert()(VerifiedReplyContext).values(id=cid,**values)
        .on_conflict_do_update(index_elements=['id'],set_=values))
    if kind!='legacy':
        db.execute(delete(VerifiedReplyContext).where(VerifiedReplyContext.context_key==key,
            VerifiedReplyContext.template_id==tid,VerifiedReplyContext.kind=='legacy'))
    return tid


def variants(db,key,kind):
    from .learned_reply_templates import MAX_AGE
    rows=db.execute(select(VerifiedReplyContext,VerifiedReplyText.text)
        .join(VerifiedReplyText,VerifiedReplyText.id==VerifiedReplyContext.template_id)
        .where(VerifiedReplyContext.context_key==key,VerifiedReplyContext.kind.in_((kind,'legacy')))
        .order_by(VerifiedReplyContext.verified_at.desc(),VerifiedReplyContext.id).limit(12)
        .execution_options(populate_existing=True)).all()
    result=[]
    for entry,text in rows:
        if entry.kind=='legacy' and time.time()-entry.verified_at>MAX_AGE:continue
        result.append({'answer':render(text,entry.presentation),'verdict':deepcopy(entry.verdict),
            'at':entry.verified_at,'source':entry.source_review_id,'template_id':entry.template_id,'durable':entry.kind!='legacy'})
    return result


def statistics(db):
    return {'unique_templates':db.scalar(select(func.count()).select_from(VerifiedReplyText)) or 0,
        'contexts':db.scalar(select(func.count()).select_from(VerifiedReplyContext)) or 0,
        'markets':dict(db.execute(select(VerifiedReplyContext.source_marketplace,func.count())
            .group_by(VerifiedReplyContext.source_marketplace)).all())}


def migrate_legacy(db):
    """Lossless transactional cache conversion; preserve expired proofs as inactive."""
    from .learned_reply_templates import _variants
    rows=list(db.scalars(select(Setting).where(Setting.key.startswith('verified-replies:')).order_by(Setting.key)))
    converted=0
    for row in rows:
        for key,entry in (row.value or {}).items():
            for item in _variants(entry,db):
                remember(db,key,'legacy',item['answer'],item.get('verdict') or {},
                    source=item.get('source',''),verified_at=float(item.get('at',0)))
                converted+=1
    # Bodies that lost a bounded context are still preserved as unapproved text.
    for row in db.scalars(select(Setting).where(Setting.key.startswith('verified-texts:'))):
        for item in (row.value or {}).values():
            if item.get('answer'):body(db,item['answer'])
    db.execute(delete(Setting).where(Setting.key.startswith('verified-replies:')))
    db.execute(delete(Setting).where(Setting.key.startswith('verified-texts:')))
    db.flush()
    return {**statistics(db),'converted_approvals':converted}
