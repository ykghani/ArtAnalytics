from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import requests_cache
from typing import Dict, Any

class AICApiClient:
    """Handles all API interactions with the Art Institute of Chicago."""
    
    def __init__(self, base_url: str, search_url: str, user_agent: str, cache_file: str):
        self.base_url = base_url
        self.search_url = search_url
        self.headers = {"AIC-User-Agent": user_agent}
        self.session = self._create_session()
        requests_cache.install_cache(cache_file, backend='sqlite')

    def _create_session(self) -> Session:
        """Create and configure a requests session with retry strategy."""
        session = Session()
        retry_strategy = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def get_artwork_page(self, page: int, params: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch a page of artwork data."""
        params['page'] = page
        response = self.session.get(self.base_url, params=params, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def get_artwork_details(self, aic_id: int, fields: str) -> Dict[str, Any]:
        """Fetch details for a specific artwork."""
        response = self.session.get(
            f"{self.base_url}/{aic_id}",
            params={'fields': fields},
            headers=self.headers
        )
        response.raise_for_status()
        return response.json()['data']

    def get_image(self, img_id: str) -> bytes:
        """Download an image from the IIIF API."""
        url = f"https://www.artic.edu/iiif/2/{img_id}/full/843,/0/default.jpg"
        response = self.session.get(url, headers=self.headers)
        response.raise_for_status()
        return response.content