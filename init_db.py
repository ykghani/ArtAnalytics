from src.database.database import Database
from src.database.models import Base
from pathlib import Path

# Initialize database
db = Database(Path('data/artwork.db'))

# Create tables
Base.metadata.create_all(db.engine)

# Get a session
session = db.get_session()

# Initialize museums
db.init_museums(session)