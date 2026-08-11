"""
Download and prepare datasets for Festival Intelligence Terminal.
"""
import os
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd


def download_huggingface_datasets():
    """Download datasets from Hugging Face."""
    print("Downloading Hugging Face datasets...")
    
    try:
        from datasets import load_dataset
        
        # Electronic Music Knowledge
        print("\n1. Downloading Electronic Music Knowledge dataset...")
        tracks = load_dataset("NaturNestAI/electronic-music-knowledge", "tracks", split="train")
        artists = load_dataset("NaturNestAI/electronic-music-knowledge", "artists", split="train")
        
        # Convert to pandas and save
        output_dir = project_root / "warehouse" / "datasets" / "electronic_music"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        tracks_df = pd.DataFrame(tracks)
        artists_df = pd.DataFrame(artists)
        
        tracks_df.to_parquet(output_dir / "tracks.parquet", index=False)
        artists_df.to_parquet(output_dir / "artists.parquet", index=False)
        
        print(f"   Saved {len(tracks_df)} tracks to {output_dir / 'tracks.parquet'}")
        print(f"   Saved {len(artists_df)} artists to {output_dir / 'artists.parquet'}")
        
        # MusicBrainz Artists
        print("\n2. Downloading MusicBrainz Artists dataset...")
        mb_artists = load_dataset("LeData/media-metadata-musicbrainz-artists", split="train")
        
        output_dir = project_root / "warehouse" / "datasets" / "musicbrainz"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        mb_artists_df = pd.DataFrame(mb_artists)
        mb_artists_df.to_parquet(output_dir / "artists.parquet", index=False)
        
        print(f"   Saved {len(mb_artists_df)} artists to {output_dir / 'artists.parquet'}")
        
        print("\n✓ Hugging Face datasets downloaded successfully")
        return True
        
    except Exception as e:
        print(f"✗ Error downloading Hugging Face datasets: {e}")
        return False


def setup_kaggle():
    """Set up Kaggle API for dataset downloads."""
    print("\nSetting up Kaggle API...")
    
    try:
        import kaggle
        
        # Check if kaggle.json exists
        kaggle_path = Path.home() / ".kaggle" / "kaggle.json"
        if not kaggle_path.exists():
            print("   Kaggle API key not found.")
            print("   Please download kaggle.json from https://www.kaggle.com/settings")
            print(f"   And place it in {kaggle_path.parent}")
            return False
        
        print("   ✓ Kaggle API configured")
        return True
        
    except ImportError:
        print("   Installing kaggle package...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "kaggle"])
        print("   ✓ Kaggle installed")
        return False
    except Exception as e:
        print(f"   ✗ Error setting up Kaggle: {e}")
        return False


def download_kaggle_datasets():
    """Download datasets from Kaggle."""
    print("\nDownloading Kaggle datasets...")
    
    if not setup_kaggle():
        print("   Skipping Kaggle downloads (API not configured)")
        return False
    
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi
        
        api = KaggleApi()
        api.authenticate()
        
        output_dir = project_root / "warehouse" / "datasets" / "kaggle"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Music Artists Popularity
        print("\n1. Downloading Music Artists Popularity dataset...")
        api.dataset_download_files(
            "pieca111/music-artists-popularity",
            path=str(output_dir / "music_artists_popularity"),
            unzip=True
        )
        print("   ✓ Downloaded Music Artists Popularity")
        
        # Spotify Artists Dataset
        print("\n2. Downloading Spotify Artists dataset...")
        api.dataset_download_files(
            "rolanddutauziet/dataset-projet-spotify",
            path=str(output_dir / "spotify_artists"),
            unzip=True
        )
        print("   ✓ Downloaded Spotify Artists")
        
        # Spotify Artists and Tracks
        print("\n3. Downloading Spotify Artists and Tracks dataset...")
        api.dataset_download_files(
            "gokulraja84/spotify-artists-and-tracks-datasets",
            path=str(output_dir / "spotify_artists_tracks"),
            unzip=True
        )
        print("   ✓ Downloaded Spotify Artists and Tracks")
        
        print("\n✓ Kaggle datasets downloaded successfully")
        return True
        
    except Exception as e:
        print(f"✗ Error downloading Kaggle datasets: {e}")
        return False


def main():
    """Main function to download all datasets."""
    print("=" * 60)
    print("Festival Intelligence Terminal - Dataset Download")
    print("=" * 60)
    
    # Download Hugging Face datasets
    hf_success = download_huggingface_datasets()
    
    # Download Kaggle datasets
    kaggle_success = download_kaggle_datasets()
    
    print("\n" + "=" * 60)
    print("Download Summary")
    print("=" * 60)
    print(f"Hugging Face: {'✓ Success' if hf_success else '✗ Failed'}")
    print(f"Kaggle: {'✓ Success' if kaggle_success else '✗ Failed'}")
    print("=" * 60)
    
    if hf_success:
        print("\nNext steps:")
        print("1. Configure Kaggle API to download Kaggle datasets")
        print("2. Update backend to use the downloaded datasets")
        print("3. Test the artist search with new data")


if __name__ == "__main__":
    main()
