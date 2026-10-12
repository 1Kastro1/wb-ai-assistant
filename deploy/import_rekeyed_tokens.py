"""Import re-encrypted credentials into the destination PostgreSQL database."""

import json
import sys
from pathlib import Path

from app.db import Account, Session, StoreProfile
from app.security import decrypt_secret


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit('Usage: import_rekeyed_tokens.py tokens.json')
    rows = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    with Session() as db:
        for row in rows:
            account_id = row['id']
            ciphertext = row['encrypted_token']
            if not isinstance(account_id, str) or not isinstance(ciphertext, str):
                raise ValueError('Invalid account data')
            if not ciphertext.startswith('fernet:') or not decrypt_secret(ciphertext):
                raise ValueError('Token encryption check failed')
            if ':' not in account_id and db.get(StoreProfile, account_id) is None:
                raise ValueError('Unknown store profile')
            if db.get(Account, account_id) is not None:
                raise ValueError('Destination account already exists')
            db.add(Account(id=account_id, encrypted_token=ciphertext))
        db.commit()
    print('Imported account IDs:', ', '.join(row['id'] for row in rows))


if __name__ == '__main__':
    main()
