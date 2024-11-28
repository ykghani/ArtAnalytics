from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
import time
from typing import Dict
from collections import defaultdict

api_key_header = APIKeyHeader(name="X-API-Key")
RATE_LIMIT_DURATION = 3600  # 1 hour
RATE_LIMIT_REQUESTS = 1000  # requests per hour

class RateLimiter:
    def __init__(self):
        self.requests: Dict[str, list] = defaultdict(list)
        
    def is_rate_limited(self, api_key: str) -> bool:
        now = time.time()
        # Remove old requests
        self.requests[api_key] = [
            req_time for req_time in self.requests[api_key]
            if now - req_time < RATE_LIMIT_DURATION
        ]
        
        # Check if over limit
        if len(self.requests[api_key]) >= RATE_LIMIT_REQUESTS:
            return True
            
        self.requests[api_key].append(now)
        return False

rate_limiter = RateLimiter()

async def verify_api_key(
    request: Request,
    api_key: str = Depends(api_key_header)
):
    # In production, verify against secure storage
    valid_keys = {"test_key", "dev_key"}  # Example only
    
    if api_key not in valid_keys:
        raise HTTPException(
            status_code=403,
            detail="Invalid API key"
        )
    
    if rate_limiter.is_rate_limited(api_key):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded"
        )
    
    return api_key