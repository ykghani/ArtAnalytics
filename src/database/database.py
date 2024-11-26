from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session
from typing import Optional
from pathlib import Path

class Database:
    def __init__(self, db_path: Path):
        self.engine = create_engine(f'sqlite:///{db_path}', echo=False)
        self._SessionFactory = sessionmaker(bind=self.engine)
        
    def create_tables(self):
        """Create all tables in the database"""
        Base.metadata.create_all(self.engine)
        
    def get_session(self) -> Session:
        """Get a new database session"""
        return self._SessionFactory()
        
    def init_museums(self, session: Session):
        """Initialize museum entries if they don't exist"""
        museums = [
            {'code': 'met', 'name': 'Metropolitan Museum of Art'},
            {'code': 'aic', 'name': 'Art Institute of Chicago'}
        ]
        
        for museum_data in museums:
            if not session.query(Museum).filter_by(code=museum_data['code']).first():
                museum = Museum(**museum_data)
                session.add(museum)
        
        session.commit()