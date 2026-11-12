"""Read-only, bounded diagnostic of the current audit's attention cases."""
import json
from sqlalchemy import select
from app.db import Session,Job,ReviewLibraryAuditRow,Review,Product,Draft,Publication,Memory
from app.review_library_audit import KIND,report
from app.owner_reply_rules import matching_owner_rules
from app.services import review_verified_facts

with Session() as db:
    job=db.scalar(select(Job).where(Job.kind==KIND).order_by(Job.created_at.desc()).limit(1))
    result={'status':report(db),'cases':[]}
    for row in db.scalars(select(ReviewLibraryAuditRow).where(ReviewLibraryAuditRow.job_id==job.id,
            ReviewLibraryAuditRow.status=='attention').order_by(ReviewLibraryAuditRow.review_id).limit(10)):
        review=db.get(Review,row.review_id)
        product=db.get(Product,review.product_id) if review else None
        draft=db.scalar(select(Draft).where(Draft.review_id==row.review_id))
        rules=matching_owner_rules(db,product) if product else []
        def fields(obj,names):return {key:getattr(obj,key,None) for key in names} if obj else None
        result['cases'].append({'audit':fields(row,['review_id','status','issues','attempts']),
            'review':fields(review,['id','wb_review_id','wb_account_id','marketplace','product_id','text','sections',
                'rating','risk','manual','safety_critical','status','is_answered','existing_answer','topics']),
            'product':fields(product,['id','name','brand','category','part_number','facts']),
            'draft':fields(draft,['id','text','quality','revision','edits','model']),
            'rules':rules,'facts':review_verified_facts(db,review,product,rules) if product else [],
            'publications':[fields(p,['id','status']) for p in db.scalars(select(Publication).where(Publication.review_id==row.review_id))]})
    result['review_rules']=[fields(m,['id','text','kind','scope']) for m in db.scalars(select(Memory).where(Memory.enabled==True,Memory.kind=='REVIEW_RULE').order_by(Memory.id))]
    print(json.dumps(result,ensure_ascii=False,default=str))
