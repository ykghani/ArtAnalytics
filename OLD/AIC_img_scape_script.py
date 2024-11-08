'''
Script that downloads all public domain images in the Art Institute of Chicago's collection and saves to a folder
The images can then serve as a starting point for other use cases (e.g., image analysis, etc.)

'''
import pandas as pd
import os
import time
import requests
from PIL import Image
from io import BytesIO
import re

aic = pd.read_json('allArtworks.jsonl', lines= True)
print_ids = aic[aic['department_title'] == 'Prints and Drawings'][['id', 'title']]

SEARCH_URL = "https://api.artic.edu/api/v1/artworks/search"
OUTPUT_FOLDER = "AIC_Prints_and_Drawings"
USER_AGENT = "AIC-ArtDownloadBot/1.0 (yusuf.k.ghani@gmail.com)"
PROGRESS_FILE = 'processed_ids.txt'
IIIF_URL = "https://www.artic.edu/iiif/2"

headers = {
    "User-Agent": "AIC-ArtDownloadBot/1.0 (your-email@example.com)"
}

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


def sanitize_filename(filename):
    """Sanitizes filenames by removing invalid characters."""
    return re.sub(r'[<>:"/\\|?*]', '', filename)

def download_img(aic_id, title):
    """Downloads image from AIC using the given image_id and saves it with a sanitized filename."""
    img_url = f"{IIIF_URL}/{aic_id}/full/843,/0/default.jpg"
    headers = {'AIC-User-Agent': USER_AGENT}
    
    response = requests.get(img_url, headers=headers)
    if response.status_code == 200:
        img = Image.open(BytesIO(response.content))
        img_filename = os.path.join(OUTPUT_FOLDER, f"{aic_id}_{sanitize_filename(title)}.jpg")
        img.save(img_filename)
        return True
    return False

print_ids = print_ids[0:5]
print(print_ids)
# Iterate over all IDs in print_ids DataFrame
# for _, row in print_ids.iterrows():
#     aic_id = str(row['id'])
#     title = row['title']

#     # Skip already processed or missing image_id
#     if aic_id in processed_ids:
#         continue
    
#     # Download image and save progress
#     if download_img(aic_id, title):
#         processed_ids.add(aic_id)
#         with open(PROGRESS_FILE, 'a') as f:
#             f.write(f"{aic_id}\n")

# print("Download complete.")
# # Define parameters for public domain artworks
# params = {
#     "query[bool][must][][term][is_public_domain]": "true",
#     "query[bool][must][][term][department_title]": "Prints and Drawings",
#     "fields": "id,title,image_id",
#     "limit": 100  # Max items per page for pagination
# }



# page = 1
# params['page'] = page
# response = requests.get(SEARCH_URL, headers= headers, params= params)

# if response.status_code != 200: 
#     print(f"Failed to retrieve page {page}: {response.status_code}")

# data = response.json()
# print(data) 


# def sanitize_filename(filename):
#     """Sanitizes filenames by removing invalid characters."""
#     return re.sub(r'[<>:"/\|?*]', '', filename)

def download_img(aic_id, title, image_id):
    """Downloads image from AIC using the given image_id and saves it with a sanitized filename."""
    img_url = f"https://www.artic.edu/iiif/2/{image_id}/full/843,/0/default.jpg"
    headers = {'User-Agent': USER_AGENT}
    
    response = requests.get(img_url, headers=headers)
    if response.status_code == 200:
        img = Image.open(BytesIO(response.content))
        img_filename = os.path.join(OUTPUT_FOLDER, f"{aic_id}_{title}.jpg")
        img.save(img_filename)
        return True
    return False

def fetch_artworks():
    """Fetches artworks from AIC API and downloads public domain images from the Prints and Drawings department."""
    headers = {"User-Agent": USER_AGENT}
    page = 1
    params = {
        "query[term][is_public_domain]": "true",
        "query[term][department_title]": "Prints and Drawings",
        "limit": 100,
        "fields": "id,title,image_id"
    }
    
    while True:
        params['page'] = page
        time.sleep(1)
        try:
            response = requests.get(SEARCH_URL, headers=headers, params=params)
            
            # Check if response status is not OK
            if response.status_code != 200:
                print(f"Request failed with status code {response.status_code}. Retrying in 5 seconds...")
                time.sleep(5)
                continue  # Retry the loop

            # Parse JSON data safely
            try:
                data = response.json()
            except requests.JSONDecodeError as e:
                print(f"Failed to decode JSON response: {e}. Retrying in 5 seconds...")
                time.sleep(5)
                continue  # Retry the loop
            
            # Stop if no more data
            if 'data' not in data or not data['data']:
                break
            
            for art in data['data']:
                aic_id = str(art['id'])
                title = sanitize_filename(art.get('title', 'untitled'))
                image_id = art.get('image_id')

                # Skip already processed or missing image
                if aic_id in processed_ids or not image_id:
                    continue
                
                # Download image and save progress
                if download_img(aic_id, title, image_id):
                    processed_ids.add(aic_id)
                    with open(PROGRESS_FILE, 'a') as f:
                        f.write(f"{aic_id}\n")
                
                time.sleep(1)  # Throttle to avoid hitting rate limits
            
            page += 1
        
        except requests.RequestException as e:
            print(f"Request failed: {e}. Retrying in 5 seconds...")
            time.sleep(5)  # Retry after a delay


