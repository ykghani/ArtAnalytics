from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path
import random
from typing import Optional, List
import sqlite3
from pydantic import BaseModel

app = FastAPI()

# Configuration
IMAGE_DIR = Path("data/images")  # Adjust this to your image directory
DB_PATH = Path("data/artwork.db")  # Adjust this to your database path

class ArtworkResponse(BaseModel):
    id: int
    title: str
    artist: str
    date_display: Optional[str]
    medium: Optional[str]
    image_path: str

def get_random_artwork() -> ArtworkResponse:
    """Get random artwork from the database"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, artist, date_display, medium, image_path 
            FROM artworks 
            WHERE image_path IS NOT NULL 
            ORDER BY RANDOM() 
            LIMIT 1
        """)
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="No artwork found")
            
        return ArtworkResponse(
            id=result[0],
            title=result[1],
            artist=result[2],
            date_display=result[3],
            medium=result[4],
            image_path=result[5]
        )

@app.get("/api/random-artwork")
async def random_artwork():
    """Return metadata for a random artwork"""
    return get_random_artwork()

@app.get("/api/random-image")
async def random_image():
    """Return a random image file"""
    artwork = get_random_artwork()
    image_path = Path(artwork.image_path)
    
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Image file not found")
        
    return FileResponse(image_path)

@app.get("/api/slideshow")
async def get_slideshow(count: int = 10):
    """Return multiple random artworks for slideshow"""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, artist, date_display, medium, image_path 
            FROM artworks 
            WHERE image_path IS NOT NULL 
            ORDER BY RANDOM() 
            LIMIT ?
        """, (count,))
        
        results = cursor.fetchall()
        return [
            ArtworkResponse(
                id=row[0],
                title=row[1],
                artist=row[2],
                date_display=row[3],
                medium=row[4],
                image_path=row[5]
            )
            for row in results
        ]

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)