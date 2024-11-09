# Art Institute of Chicago Image Downloader

A Python script to download public domain images from the Art Institute of Chicago's API. This project provides functionality to systematically download and manage artwork images, with robust error handling and detailed logging.

## Features

- Downloads public domain images from AIC's API
- Handles API rate limiting and network issues
- Tracks download progress and can resume interrupted downloads
- Sanitizes filenames for cross-platform compatibility
- Provides detailed logging of the download process
- Includes error recovery and retry mechanisms
- Generates reports for failed downloads

## Project Structure

```
ArtAnalytics/
├── src/                # Source code
│   ├── __init__.py
│   ├── download.py    # Main download functionality
│   └── utils.py       # Utility functions
├── data/              # Data directory (not tracked in Git)
│   ├── raw/          # Downloaded images
│   └── processed/    # Processed data files
├── logs/              # Log files (not tracked in Git)
├── notebooks/         # Jupyter notebooks for analysis
├── requirements.txt   # Project dependencies
├── main.py           # Main execution script
└── README.md         # This file
```

## Setup

1. **Clone the repository**
```bash
git clone https://github.com/yourusername/ArtAnalytics.git
cd ArtAnalytics
```

2. **Create and activate virtual environment**
```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate
```

3. **Install dependencies**
```bash
pip install -r requirements.txt
```

4. **Configure API settings**
- Update the `USER_AGENT` in `src/download.py` with your email address
- Adjust rate limiting settings if needed

## Usage

### Basic Usage

Run the main script to start downloading images:
```bash
python main.py
```

### Recovering Failed Downloads

To retry failed downloads:
```python
from src.download import AICDownloader

downloader = AICDownloader()
downloader.recover_failed_downloads()
```

### Downloading Specific Artworks

To download specific artwork IDs:
```python
from src.download import AICDownloader

downloader = AICDownloader()
id_list = [123456, 789012]  # Replace with desired AIC IDs
downloader.download_specific_ids(id_list)
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
- `aic_downloader.log`: Main application log
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

## Dependencies

Main dependencies include:
- requests
- Pillow (PIL)
- requests-cache
- logging

See `requirements.txt` for complete list.

## Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- Art Institute of Chicago for providing the public API
- [AIC API Documentation](https://api.artic.edu/docs/)

## Notes

- Respect API rate limits
- Some images may be large, ensure adequate storage space
- Check logs regularly for any issues
- Keep the virtual environment active while running scripts

## Contact

Yusuf Ghani - [yusuf.k.ghani@gmail.com](mailto:yusuf.k.ghani@gmail.com)

Project Link: https://github.com/ykghani/ArtAnalytics
