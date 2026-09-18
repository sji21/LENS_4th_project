"""MySQL connections for knowledge data, independent of Django's account DB."""
from __future__ import annotations

import hashlib
import os

import sqlalchemy as sa
from sqlalchemy.engine import URL

from src.database.mysql_schema import METADATA, VERSIONS, SCHEMA_VERSION, schema_sql


def configured_engine():
    required = ("LENS_MYSQL_HOST", "LENS_MYSQL_DATABASE", "LENS_MYSQL_USER", "LENS_MYSQL_PASSWORD")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise ValueError("MySQL 설정 누락: " + ", ".join(missing))
    port = int(os.getenv("LENS_MYSQL_PORT", "3306"))
    if not 1 <= port <= 65535:
        raise ValueError("MySQL 포트는 1..65535 범위여야 합니다.")
    connect_args = {"connect_timeout": 10, "read_timeout": 120, "write_timeout": 120}
    if ca := os.getenv("LENS_MYSQL_SSL_CA"):
        connect_args.update(ssl_ca=ca, ssl_verify_cert=True, ssl_verify_identity=True)
    url = URL.create("mysql+pymysql", username=os.environ["LENS_MYSQL_USER"],
        password=os.environ["LENS_MYSQL_PASSWORD"], host=os.environ["LENS_MYSQL_HOST"],
        port=port, database=os.environ["LENS_MYSQL_DATABASE"], query={"charset": "utf8mb4"})
    engine = sa.create_engine(url, pool_pre_ping=True, pool_recycle=1800,
        connect_args=connect_args, isolation_level="REPEATABLE READ", hide_parameters=True)

    @sa.event.listens_for(engine, "connect")
    def strict_session(connection, _):
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION sql_mode='STRICT_ALL_TABLES,NO_ENGINE_SUBSTITUTION'")
            cursor.execute("SET SESSION time_zone='+00:00'")
    return engine


def initialize(engine):
    if engine.dialect.name != "mysql":
        raise ValueError("이 저장소는 실제 MySQL에서 초기화해야 합니다.")
    ddl_hash = hashlib.sha256(schema_sql().encode()).hexdigest()
    with engine.connect() as connection:
        version = str(connection.exec_driver_sql("SELECT VERSION()").scalar_one())
        if "mariadb" in version.lower() or tuple(int(x) for x in version.split(".")[:2]) < (8, 4):
            raise ValueError("지식 저장소에는 MySQL 8.4 이상이 필요합니다.")
        if sa.inspect(connection).has_table(VERSIONS.name):
            stored = connection.execute(sa.select(VERSIONS)).mappings().all()
            if stored and [(r["version"], r["ddl_sha256"]) for r in stored] != [(SCHEMA_VERSION, ddl_hash)]:
                raise ValueError("MySQL 스키마 변경 이력이 다릅니다. 명시적인 마이그레이션이 필요합니다.")
    # MySQL DDL commits implicitly. Keep it outside data-import transactions.
    METADATA.create_all(engine)
    with engine.begin() as connection:
        if not connection.execute(sa.select(VERSIONS)).first():
            connection.execute(VERSIONS.insert().values(version=SCHEMA_VERSION, ddl_sha256=ddl_hash))
    return {"mysql_version": version, "schema_version": SCHEMA_VERSION, "ddl_sha256": ddl_hash}
