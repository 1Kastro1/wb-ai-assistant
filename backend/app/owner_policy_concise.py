"""Replace three exact owner review rules; retain history and saved drafts."""
import argparse
import json
from pathlib import Path
from .db import Session
from .owner_policy_cleanup import plan, apply

POLICY = json.loads(Path(__file__).with_suffix('.json').read_text(encoding='utf-8'))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--actor')
    args = parser.parse_args()
    with Session() as db:
        result = apply(db, args.actor, policy=POLICY) if args.apply else {
            'changes': len(plan(db, POLICY)), 'version': POLICY['version']}
        print(json.dumps(result, ensure_ascii=False), flush=True)
