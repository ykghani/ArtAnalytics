'''
Script that downloads all images from the Art Institute of Chicago's archive that is in the public domain to one's local device
'''
import requests
import os
import time
import json
from io import BytesIO
from PIL import Image
import re
import requests_cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

requests_cache.install_cache(backend= 'memory')

SEARCH_URL = "https://api.artic.edu/api/v1/artworks/search"
OUTPUT_FOLDER = "AIC_Prints_and_Drawings"
USER_AGENT = "AIC-ArtDownloadBot/1.0 (yusuf.k.ghani@gmail.com)"
PROGRESS_FILE = 'processed_ids.json'

headers = {
    "AIC-User-Agent": "ArtDownloadBot/1.0 (yusuf.k.ghani@gmail.com)"
}

def sanitize_filename(filename):
    """Sanitizes filenames by removing invalid characters."""
    return re.sub(r'[<>:"/\\|?*]', '', filename)

download_log = {
    "success": [],
    "failed": [],
    "network_error": [],
    "image_processing_error": [],
    "other_error": {},
    "all": set()
}

def log_status(aic_id, status, error_message= None):
    """Logs the status of each ID to the download_log dictionary and writes it to JSON file."""
    if status == "success":
        download_log["success"].append(aic_id)
    else:
        download_log["failed"].append(aic_id)
        if error_message:
            # Store specific errors
            if status not in download_log["other_error"]:
                download_log["other_error"][status] = {}
            download_log["other_error"][status][aic_id] = error_message
    
    download_log['all'].add(aic_id)
    
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(download_log, f, indent=4)

# def write_processed_id(id):
#     with open(PROGRESS_FILE, 'a') as f:
#         f.write(f"{id}\n")
        
def download_img(aic_id, img_id, title, artist, session): 
    IIIF_URL = f"https://www.artic.edu/iiif/2/{img_id}/full/843,/0/default.jpg"
    
    try:
        img_response = session.get(IIIF_URL, headers= headers)
        if img_response.status_code == 200: 
            try:
                image = Image.open(BytesIO(img_response.content))
                img_filename = os.path.join(OUTPUT_FOLDER, f"{aic_id}_{sanitize_filename(title)}_{artist}.jpg")
                image.save(img_filename)
                print(f"AIC ID {aic_id} successfully downloaded")
                log_status(aic_id, "success")
            except Exception as e: 
                print(f"Error opening image ID {aic_id}: {e}")
                log_status(aic_id, "image_processing_error", str(e))
        else:
            print(f"Failed to download AIC ID: {aic_id}. Status code: {img_response.status_code}")
            log_status(aic_id, "failed", f"HTTP error code: {img_response.status_code}")
    except requests.exceptions.RequestException as e: 
        print(f"Network error downloading AIC ID: {aic_id}")
        log_status(aic_id, 'network_error', str(e))
    

# Create a session with retry capability
session = requests.Session()
retry_strategy = Retry(
    total=5,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS"]
)

adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

# Ensure output and progress directories exist
if not os.path.exists(OUTPUT_FOLDER):
    os.makedirs(OUTPUT_FOLDER)
    print(f"Folder {OUTPUT_FOLDER} created")
    

# Load processed IDs if progress file exists, otherwise initialize empty set
if os.path.exists(PROGRESS_FILE):
    with open(PROGRESS_FILE, 'r') as f: 
        processed_ids = set(line.strip() for line in f) 
else:
    processed_ids = set()
    

url = 'https://api.artic.edu/api/v1/artworks'
fields = ['id', 'title', 'artist_display', 'image_id', 'department_title']
params = {'is_public_domain': 'true', 'department_title': 'Prints and Drawings', 'fields': ','.join(fields), 'limit': 100}


page = 1
while True: 
    params['page'] = page
    response = requests.get(url, params= params, headers= headers)
    
    try:
        data = response.json()  # Convert the JSON response to a dictionary
    except ValueError:
        print("Error: Response is not valid JSON. Exiting loop.")
        break
    
    if 'data' not in data:
        print("Error: 'data' field not found in JSON response. Exiting loop.")
        break

    for art in data['data']:
        aic_id = art['id']
        img_id = art['image_id']
        title = art['title']
        artist = art['artist_display']
        
        if aic_id in processed_ids:
            print(f"ID {aic_id} already processed. SKIPPING!")
            continue
        elif art['department_title'] != 'Prints and Drawings':
            # processed_ids.add(aic_id)
            write_processed_id(aic_id)
            continue
        
        time.sleep(1)
        download_img(aic_id, img_id, title, artist)
        # IIIF_URL = f"https://www.artic.edu/iiif/2/{img_id}/full/843,/0/default.jpg"
        # img_response = requests.get(IIIF_URL, headers= headers)
        
        # if img_response.status_code == 200: 
        #     try:
        #         image = Image.open(BytesIO(img_response.content))
        #         img_filename = os.path.join(OUTPUT_FOLDER, f"{aic_id}_{sanitize_filename(title)}_{artist}.jpg")
        #         image.save(img_filename)
        #         print(f"AIC ID {aic_id} successfully downloaded")
        #     except Exception as e: 
        #         print(f"Error opening image ID {aic_id}")
            
            # processed_ids.add(aic_id)
            write_processed_id(aic_id)
        
    time.sleep(1) #To conform with api ping request guidelines
    page += 1
