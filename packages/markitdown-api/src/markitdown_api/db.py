from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_options(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


engine = create_engine(
    get_settings().database_url,
    **_engine_options(get_settings().database_url),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def initialize_database() -> None:
    migration_dir = Path(__file__).parent / "migrations"
    config = Config()
    config.set_main_option("script_location", str(migration_dir))
    config.set_main_option(
        "sqlalchemy.url", get_settings().database_url.replace("%", "%%")
    )
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        inspector = inspect(connection)
        has_existing_api_schema = inspector.has_table("accounts")
        has_version_table = inspector.has_table("alembic_version")
        connection.commit()
        if has_existing_api_schema and not has_version_table:
            command.stamp(config, "0001_base")
            connection.commit()
        command.upgrade(config, "head")
