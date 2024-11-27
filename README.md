# Art Institute of Chicago Image Downloader

A Python application for downloading and managing artwork data and images from major museum APIs, including the Metropolitan Museum of Art, Art Institute of Chicago, and Cleveland Museum of Art. More museums will be added over time 

## Features

Multi-museum support with standardized data model
Parallel downloading of artwork data and images
Resume-capable downloads with progress tracking
SQLite database for storing artwork metadata
Rate limiting and error handling
Support for public domain image downloads
Museum-specific image processing and filename generation

## Project Structure

```
├── data/                      # Data storage
│   ├── aic/                  # AIC-specific data
│   ├── met/                  # MET-specific data 
│   └── cma/                  # CMA-specific data
├── src/
│   ├── museums/             # Museum API clients
│   │   ├── aic.py
│   │   ├── met.py 
│   │   ├── cma.py
│   │   └── schemas.py
│   ├── database/            # Database models and operations
│   │   ├── models.py
│   │   └── repository.py
│   ├── download/            # Download management
│   │   ├── artwork_downloader.py
│   │   └── progress_tracker.py
│   └── utils.py             # Shared utilities
├── alembic/                  # Database migrations
├── tests/                    # Test suite
├── poetry.lock              # Poetry lock file
├── pyproject.toml           # Project metadata and dependencies
└── README.md
```

## Requirements
1. Python 3.12+
2. Poetry for dependency management

## Setup

1. **Clone the repository**
```bash
git clone https://github.com/yourusername/art-museum-collection.git
cd art-museum-collection
```

2. **Install dependencies using poetry**
```bash
poetry install
```

3. **Configure settings**
Create a .env file in your project root with downloader settings specified
```env
# Logging
LOG_LEVEL=verbose

# Download settings
BATCH_SIZE=100
RATE_LIMIT_DELAY=1.0 
ERROR_RETRY_DELAY=5.0
MAX_RETRIES=5

# Museum API settings
AIC_USER_AGENT=YourUserAgent/1.0
MET_RATE_LIMIT=80.0
```

4. **Configure API settings**
- Update the `USER_AGENT` in `src/download.py` with your email address
- Adjust rate limiting settings if needed

## Usage

### Basic Usage

Run the main script to start downloading images for all museums
```bash
poetry run python main.py 
```

### Downloading from a specific museum 

To retry failed downloads:
```bash
poetry run python main.py aic # could also pass in met or cma 
```

## Error Handling

The script handles several types of errors:
- Network timeouts and connection issues
- API rate limiting
- File system errors (long filenames, permissions)
- Missing image IDs
- Invalid responses

Failed downloads are logged and can be retried using the recovery functions.

## Logging

Logs are stored in the `logs/` directory:
- Detailed download progress and error information
- Download statistics and summaries

## File Naming Convention

Downloaded files follow the format:
```
{aic_id}_{truncated_title}_{artist}.jpg
```

Example:
```
123456_Starry Night_Vincent van Gogh.jpg
```


## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Art Institute of Chicago, Metropolitan Museum of Art, and Cleveland Museum of Art for providing the public APIs
- [AIC API Documentation](https://api.artic.edu/docs/)
- [Met API Documentation](https://metmuseum.github.io)
- [CMA API Documentation](https://www.clevelandart.org/open-access-api)

## Notes

- Respect API rate limits
- Some images may be large, ensure adequate storage space
- Check logs regularly for any issues
- Keep the virtual environment active while running scripts

## Contact

Yusuf Ghani - [yusuf.k.ghani@gmail.com](mailto:yusuf.k.ghani@gmail.com)

Project Link: https://github.com/ykghani/ArtAnalytics
