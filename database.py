from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE")
if DATABASE_URL and not DATABASE_URL.startswith("sqlite"):
    DATABASE_URL = f"sqlite:///{DATABASE_URL}"

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def upgrade_db():
    from sqlalchemy import inspect, text
    import models
    inspector = inspect(engine)
    
    with engine.begin() as conn:
        for table_name, table in models.Base.metadata.tables.items():
            if not inspector.has_table(table_name):
                # New table, create_all will handle it
                continue
            
            existing_columns = [c['name'] for c in inspector.get_columns(table_name)]
            for column in table.columns:
                if column.name not in existing_columns:
                    col_type = column.type.compile(engine.dialect)
                    # For SQLite, we can only add columns one by one
                    # We use simple ALTER TABLE. For complex migrations, use Alembic.
                    # Handle boolean type correctly for SQLite ADD COLUMN
                    type_str = str(col_type)
                    if engine.dialect.name == 'sqlite' and type_str == 'BOOLEAN':
                        type_str = 'INTEGER'
                    
                    alter_cmd = f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {type_str}'
                    print(f"Upgrading database: {alter_cmd}")
                    conn.execute(text(alter_cmd))
