from app.db import Session, Notification, StoreProfile, User


def test_owner_creates_marketplace_store_and_assigns_rights(client):
    created = client.post('/stores', json={'name':'Ozon магазин','provider':'ozon','client_id':'123'} )
    assert created.status_code == 200
    assert created.json()['provider'] == 'ozon'
    store_id = created.json()['id']
    user = client.post('/users', json={'username':'ozon-new','display_name':'Ozon New','password':'temporary-password-123','position':'Менеджер Ozon'}).json()
    changed = client.patch('/users/'+user['id'], json={'permissions':['data:read','assistant:use','ozon:operate'],'store_ids':[store_id]})
    assert changed.status_code == 200
    assert changed.json()['store_ids'] == [store_id]
    assert 'ozon:operate' in changed.json()['permissions']


def test_notifications_and_team_dashboard(client):
    with Session() as db:
        db.add(Notification(id='notice-1', kind='job_failed', title='Ошибка', message='Проверьте API', severity='error'))
        db.commit()
    result = client.get('/notifications').json()
    assert result['unread'] == 1
    assert result['items'][0]['title'] == 'Ошибка'
    assert client.post('/notifications/read-all').status_code == 200
    assert client.get('/notifications').json()['unread'] == 0
    dashboard = client.get('/team/dashboard')
    assert dashboard.status_code == 200
    assert dashboard.json()['totals']['users'] >= 1
