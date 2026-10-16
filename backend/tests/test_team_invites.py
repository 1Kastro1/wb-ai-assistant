from app.db import Session, User
from sqlalchemy import select

def test_legacy_invite_cannot_bypass_owner_approval(client):
    response=client.post('/auth/invites',json={'position':'Менеджер Ozon','store_ids':['*']})
    assert response.status_code==200,response.text
    token=response.json()['invite']
    assert client.get('/auth/invites/'+token).json()['position']=='Менеджер Ozon'
    payload={'invite':token,'username':'colleague.ozon','display_name':'Коллега','password':'unique-password-12345','confirmation':'unique-password-12345'}
    assert client.post('/auth/register',json=payload).status_code==422
    with Session() as db:assert db.scalar(select(User).where(User.username=='colleague.ozon')) is None

def test_invalid_invite_is_rejected(client):
    assert client.get('/auth/invites/'+'x'*40).status_code==404
    assert client.post('/auth/register',json={'invite':'x'*40,'username':'stranger','password':'unique-password-12345'}).status_code==422
