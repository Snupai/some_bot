import sqlite3

from services.authz import Authorizer


def make_db(path, ids):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE allowed_users (user_id INTEGER PRIMARY KEY)")
        db.executemany("INSERT INTO allowed_users VALUES (?)", [(i,) for i in ids])


async def test_listed_and_owner(tmp_path):
    p = tmp_path / "allowed_users.sqlite"
    make_db(p, [111])
    az = Authorizer(p, owner_id=999)
    assert await az.is_authorized(111)
    assert await az.is_authorized(999)
    assert not await az.is_authorized(222)
    assert not await az.is_authorized(None)


async def test_missing_db_fails_closed_and_is_not_created(tmp_path):
    p = tmp_path / "nope.sqlite"
    az = Authorizer(p)
    assert not await az.is_authorized(1)
    assert not p.exists()


async def test_missing_table_fails_closed(tmp_path):
    p = tmp_path / "x.sqlite"
    sqlite3.connect(p).close()
    assert not await Authorizer(p).is_authorized(1)


async def test_async_owner_check(tmp_path):
    async def owner(uid):
        return uid == 5

    az = Authorizer(tmp_path / "none.sqlite", owner_check=owner)
    assert await az.is_authorized(5)
    assert not await az.is_authorized(6)
    assert az.is_authorized_cached(5, {5})
    assert not az.is_authorized_cached(6, {5})
