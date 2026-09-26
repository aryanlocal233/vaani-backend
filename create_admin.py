"""One-off admin user bootstrap: python create_admin.py <username> <password>
Safe to re-run -- upserts the password hash for an existing username."""
import asyncio
import sys

import bcrypt
from dotenv import load_dotenv

import db

load_dotenv()


async def main(username: str, password: str) -> None:
    await db.init_pool()
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    await db.create_admin_user(username, password_hash)
    await db.close_pool()
    print(f"Admin user '{username}' created/updated.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python create_admin.py <username> <password>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
