# =========================================================
# 🗄️ لایه سازگاری با sqlite3 برای اتصال به Turso
# =========================================================
#
# این فایل جای sqlite3 را می‌گیرد اما همان رفتار را شبیه‌سازی می‌کند:
#   conn = db()
#   cur = conn.cursor()
#   cur.execute(sql, params)
#   cur.fetchone() / cur.fetchall()  -> شبیه sqlite3.Row (هم با نام ستون، هم با ایندکس)
#   cur.lastrowid
#   conn.commit() / conn.close()
#
# بنابراین بقیه‌ی کد bot.py دست‌نخورده باقی می‌ماند و فقط این فایل
# به‌جای فایل محلی mrbeni.db، به دیتابیس آنلاین Turso وصل می‌شود.

import os
import libsql_client

TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

if not TURSO_DATABASE_URL or not TURSO_AUTH_TOKEN:
    raise RuntimeError(
        "❌ متغیرهای محیطی TURSO_DATABASE_URL و TURSO_AUTH_TOKEN تنظیم نشده‌اند.\n"
        "این‌ها را در Environment Variables سرویس (مثلاً Render) اضافه کن."
    )

# یک کلاینت مشترک برای کل عمر برنامه؛ چون گران است هر بار
# یک اتصال جدید به Turso باز شود، این کلاینت یک‌بار ساخته می‌شود
# و در همه‌ی فراخوانی‌های db() استفاده می‌گردد.
_client = libsql_client.create_client_sync(
    url=TURSO_DATABASE_URL,
    auth_token=TURSO_AUTH_TOKEN,
)


class Row:
    """شبیه‌سازی sqlite3.Row: هم row['name'] کار می‌کند، هم row[0]."""

    __slots__ = ("_columns", "_values")

    def __init__(self, columns, values):
        self._columns = columns
        self._values = values

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        try:
            idx = self._columns.index(key)
        except ValueError:
            raise KeyError(key)
        return self._values[idx]

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, IndexError):
            return default

    def keys(self):
        return list(self._columns)

    def __contains__(self, key):
        return key in self._columns

    def __repr__(self):
        return f"<Row {dict(zip(self._columns, self._values))}>"


class Cursor:
    def __init__(self, client):
        self._client = client
        self._result = None
        self._index = 0

    def execute(self, sql, params=None):
        params = list(params) if params else []
        self._result = self._client.execute(sql, params)
        self._index = 0
        return self

    def fetchone(self):
        if self._result is None or self._index >= len(self._result.rows):
            return None
        row = Row(self._result.columns, list(self._result.rows[self._index]))
        self._index += 1
        return row

    def fetchall(self):
        if self._result is None:
            return []
        rows = [
            Row(self._result.columns, list(r))
            for r in self._result.rows[self._index:]
        ]
        self._index = len(self._result.rows)
        return rows

    @property
    def lastrowid(self):
        return self._result.last_insert_rowid if self._result else None

    @property
    def rowcount(self):
        return self._result.rows_affected if self._result else 0


class Connection:
    def __init__(self, client):
        self._client = client

    def cursor(self):
        return Cursor(self._client)

    def commit(self):
        # هر execute روی Turso بلافاصله اعمال می‌شود (autocommit)،
        # پس commit فقط برای سازگاری با کد قبلی است و کاری نمی‌کند.
        pass

    def close(self):
        # کلاینت مشترک است و باید تا پایان عمر برنامه باز بماند،
        # پس اینجا واقعاً چیزی بسته نمی‌شود.
        pass


def db():
    return Connection(_client)