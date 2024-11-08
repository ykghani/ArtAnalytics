from src.download import AICDownloader

def main():
    downloader = AICDownloader()
    downloader.download_all_artwork()

if __name__ == "__main__":
    main()