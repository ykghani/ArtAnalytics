# Changelog

All notable changes to this project will be documented in this file.

## [0.1.1] - 2026-04-02

### Added
- Minneapolis Institute of Art (MIA) museum downloader with Playwright-based web scraping
- MIA uses a git repository for metadata distribution, Playwright to extract CDN image URLs
- Full-resolution URL upgrade: attempts `_full.jpg` variant, falls back to web version (`_800.jpg`)
- Classification filter for MIA artworks — only 2D formats suitable for digital display
- `_verify_url_exists()` helper for CDN URL validation
- MIA registered in museum seed data (`database.py`)
- `MIA_INTEGRATION_GUIDE.md` documenting the full MIA integration approach

### Fixed
- AIC `is_highlight` was hardcoded to `False` — now correctly reads from API response

