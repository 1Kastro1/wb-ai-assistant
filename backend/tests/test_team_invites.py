from app.db import Session, User


def test_colleague_registers_once_with_invite(client):
    response = client.post('/auth/invites', json={'position': 'Менеджер Ozon', 'store_ids': ['*']})
    assert response.status_code == 200, response.text
    token = response.json()['invite']
    assert client.get('/auth/invites/' + token).json()['position'] == 'Менеджер Ozon'

    payload = {'invite': token, 'username': 'colleague.ozon', 'display_name': 'Коллега ОЗОН',
               'password': 'unique-password-12345', 'confirmation': 'unique-password-12345'}
    registered = client.post('/auth/register', json=payload)
    assert registered.status_code == 200, registered.text
    assert registered.json()['user']['role'] == 'member'
    assert registered.json()['user']['position'] == 'Менеджер Ozon'
    with Session() as db:
        user = db.get(User, registered.json()['user']['id'])
        assert user.password_hash != payload['password']
        assert user.store_ids == ['*']

    reused = client.post('/auth/register', json={**payload, 'username': 'another.colleague'})
    assert reused.status_code == 404
    assert client.get('/auth/invites/' + token).status_code == 404


def test_colleague_cannot_register_without_valid_invite(client):
    result = client.post('/auth/register', json={'invite': 'x' * 40, 'username': 'stranger',
                         'display_name': 'Посторонний', 'password': 'unique-password-12345',
                         'confirmation': 'unique-password-12345'})
    assert result.status_code == 404
