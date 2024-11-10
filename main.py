import logging
from pathlib import Path 

from src.download import AICDownloader
from src.config import settings

def main():
    
    #Initialize settings
    project_root = Path(__file__).parent
    settings.initialize_paths(project_root)
    
    #Create and run downloader
    downloader = AICDownloader()
    downloader.download_all_artwork()

if __name__ == "__main__":
    main()