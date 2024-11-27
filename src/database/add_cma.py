from src.database.database import Database
from src.config import settings

def add_cma_museum():
    """Add CMA museum to existing database"""
    db = Database(settings.database_path)
    session = db.get_session()
    
    try:
        from src.database.models import Museum
        
        # Check if CMA already exists
        if not session.query(Museum).filter_by(code='cma').first():
            cma = Museum(code='cma', name='Cleveland Museum of Art')
            session.add(cma)
            session.commit()
            print("Successfully added CMA to database")
        else:
            print("CMA already exists in database")
            
    except Exception as e:
        print(f"Error adding CMA: {e}")
        session.rollback()
    finally:
        session.close()

if __name__ == "__main__":
    add_cma_museum()