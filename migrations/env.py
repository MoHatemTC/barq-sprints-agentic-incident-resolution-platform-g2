import os
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool
from dotenv import load_dotenv


# Add project root to Python path
sys.path.append(
    os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            ".."
        )
    )
)


load_dotenv()


# Import Base first, then import models
# Importing models registers all tables inside Base.metadata
from src.db.database import Base
import src.db.models  # noqa: F401


config = context.config


DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not set in environment variables"
    )


# Escape % because Alembic uses ConfigParser internally
config.set_main_option(
    "sqlalchemy.url",
    DATABASE_URL.replace("%", "%%")
)


# Alembic compares database schema with these models
target_metadata = Base.metadata


def run_migrations_offline():

    url = config.get_main_option(
        "sqlalchemy.url"
    )

    context.configure(
        url=url,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        include_schemas=True,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named"
        },
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():

    connectable = engine_from_config(
        config.get_section(
            config.config_ini_section
        ),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()