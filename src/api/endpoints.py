from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi_cache.decorator import cache
from sqlalchemy.orm import Session
from typing import Optional

from ..database.database import Database
from ..database.models import Artwork
from ..database.repository import ArtworkRepository
from .schemas import ClientType, ArtworkResponse
from .dependencies import verify_api_key

router = APIRouter()

def get_db():
    db = Database()
    try:
        session = db.get_session()
        yield session
    finally:
        session.close()

@router.get("/artworks/random", response_model=ArtworkResponse)
@cache(expire=60)  # Cache for 1 minute
async def get_random_artwork(
    client: ClientType = Query(..., description="Client type requesting the artwork"),
    resolution: Optional[str] = Query(None, regex="^\\d+x\\d+$"),
    db: Session = Depends(get_db),
    api_key: str = Depends(verify_api_key)
):
    try:
        repo = ArtworkRepository(db)
        artwork = repo.get_random_artwork(client, resolution)
        
        if not artwork:
            raise HTTPException(
                status_code=404,
                detail="No artwork found matching criteria"
            )
            
        # Build response
        response = ArtworkResponse(
            id=artwork.original_id,
            title=artwork.title,
            artist=artwork.artist,
            date_created=artwork.date_created,
            medium=artwork.medium,
            dimensions=artwork.dimensions,
            credit_line=artwork.credit_line,
            department=artwork.department,
            museum=artwork.museum.name,
            image_urls={
                "original": {
                    "url": f"/images/{artwork.image_path}",
                    "format": "jpeg"
                }
            },
            last_updated=artwork.updated_at
        )
        
        return response
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )