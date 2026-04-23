"""Phase 1 end-to-end smoke check.

Reads a user's encrypted token from the DB, decrypts it, calls VK users.get
over the network, and prints the result. If this succeeds, it means the whole
OAuth pipeline (redirect → fragment handover → encrypt → persist → decrypt →
VK API) works.

Run from project root:
    python scripts/smoke_test.py [--vk-user-id 12345]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import select

from app.crypto import decrypt_token
from app.db import connection
from app.models import UserStatus, users
from app.vk.api import users_get


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--vk-user-id",
        type=int,
        default=None,
        help="Target vk_user_id. Defaults to the first active user in DB.",
    )
    args = parser.parse_args()

    with connection() as conn:
        q = select(
            users.c.id,
            users.c.vk_user_id,
            users.c.display_name,
            users.c.status,
            users.c.encrypted_token,
        )
        if args.vk_user_id is not None:
            q = q.where(users.c.vk_user_id == args.vk_user_id)
        else:
            q = q.where(users.c.status == UserStatus.ACTIVE).limit(1)
        row = conn.execute(q).first()

    if row is None:
        print(
            "No matching user in DB. Complete OAuth first at http://localhost:8765",
            file=sys.stderr,
        )
        return 1

    print(
        f"Local row: id={row.id} vk_user_id={row.vk_user_id} "
        f"name={row.display_name!r} status={row.status}"
    )
    token = decrypt_token(row.encrypted_token)
    print(f"Decrypted token length: {len(token)} chars (value not printed)")

    profile = await users_get(token, user_ids=[row.vk_user_id])
    print("VK users.get response:")
    print(json.dumps(profile, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
