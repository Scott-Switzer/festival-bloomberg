# Free Music and Artist Datasets

Comprehensive list of free music and artist datasets for the Festival Intelligence Terminal.

## Hugging Face Datasets

### 1. Electronic Music Knowledge
- **Source**: https://huggingface.co/datasets/NaturNestAI/electronic-music-knowledge
- **Size**: 18.3M tracks, 1.4M artists, 353K labels, 832 genres
- **License**: CC0 1.0 (Public Domain)
- **Format**: Hugging Face datasets (can load with `load_dataset()`)
- **Features**: 
  - Tracks: title, artist, genre/style, label, year, country
  - Artists: primary genres, labels, country, active years, track count
  - Labels: genres, country, founding year
  - Genres: 832 electronic genre taxonomy
- **Download**: 
  ```python
  from datasets import load_dataset
  tracks = load_dataset("NaturNestAI/electronic-music-knowledge", "tracks", split="train")
  artists = load_dataset("NaturNestAI/electronic-music-knowledge", "artists", split="train")
  ```

### 2. MusicBrainz Artists (Hugging Face)
- **Source**: https://huggingface.co/datasets/LeData/media-metadata-musicbrainz-artists
- **Size**: Large MusicBrainz artist dataset
- **License**: CC0 1.0
- **Format**: Hugging Face datasets
- **Features**: mb_id, name, sort_name, type, gender, country, area, begin_date, end_date, aliases, tags, IPI codes, ISNI codes
- **Download**: 
  ```python
  from datasets import load_dataset
  dataset = load_dataset("LeData/media-metadata-musicbrainz-artists")
  ```

### 3. Jazz Artists (Hugging Face)
- **Source**: https://huggingface.co/datasets/LeData/media-metadata-jazz-artists
- **Size**: Jazz artist dataset
- **License**: CC0 1.0
- **Format**: Hugging Face datasets
- **Features**: artist_slug, name, genres, genre, country, bio, url, n_albums

### 4. Embeat 45M Spotify Tracks
- **Source**: https://huggingface.co/datasets/GD-Studio/embeat_45m_spotify_tracks
- **Size**: 45 million Spotify tracks
- **License**: CC BY-NC 4.0 (Non-commercial only)
- **Format**: Requires acceptance to access
- **Features**: Track metadata, artist info, album metadata, Spotify audio features
- **Note**: FOR STUDY PURPOSES ONLY - DO NOT USE COMMERCIALLY

## Kaggle Datasets

### 1. Music Artists Popularity
- **Source**: https://www.kaggle.com/datasets/pieca111/music-artists-popularity
- **Size**: 1.4 million artists
- **License**: CC BY-SA 4.0
- **Format**: CSV (201.1 MB)
- **Features**: Artist names, tags, popularity (listeners/scrobbles) from MusicBrainz and Last.fm
- **Download**: Direct CSV download from Kaggle

### 2. Spotify Artists Dataset
- **Source**: https://www.kaggle.com/datasets/rolanddutauziet/dataset-projet-spotify
- **Size**: Artist information from Spotify
- **License**: MIT
- **Format**: CSV (64.89 MB)
- **Features**: id, followers, genres, name, popularity (0-100)
- **Download**: Direct CSV download from Kaggle

### 3. Spotify Artist Feature Collaboration Network
- **Source**: https://www.kaggle.com/datasets/jfreyberg/spotify-artist-feature-collaboration-network
- **Size**: ~20k chart artists + 136k feature artists
- **License**: CC BY 4.0
- **Format**: Network dataset
- **Features**: Artist collaboration network with 135k+ musicians and 300k+ collaboration edges
- **Download**: Direct download from Kaggle

### 4. Discogs Data Dumps (February 2025)
- **Source**: https://www.kaggle.com/datasets/ofurkancoban/discogs-data-dumps-february-2025
- **Size**: 27.9 GB total
- **License**: CC0 (Public Domain)
- **Format**: CSV files
- **Features**: 
  - artists.csv (1.11 GB)
  - labels.csv
  - masters.csv
  - releases.csv
- **Download**: Direct CSV download from Kaggle

### 5. Spotify Artists and Tracks Datasets
- **Source**: https://www.kaggle.com/datasets/gokulraja84/spotify-artists-and-tracks-datasets
- **Size**: 173.34 MB total
- **License**: Unknown (check dataset page)
- **Format**: CSV files
- **Features**: artists.csv (61.97 MB), tracks.csv
- **Download**: Direct CSV download from Kaggle

## GitHub Datasets

### 1. MusicMoveArr/Datasets
- **Source**: https://github.com/MusicMoveArr/Datasets
- **Size**: Massive multi-platform dataset
- **Format**: CSV (10.2GB packed, 178GB unpacked), SQL (21.7GB packed, 149GB unpacked)
- **Features**: 
  - Deezer: 9.4M artists, 41.6M albums, 206.1M tracks
  - Tidal: 8.1M artists, 29.9M albums, 117.9M tracks
  - Spotify: 1.2M artists, 3.7M albums, 18M tracks
  - MusicBrainz: 2.7M artists, 5.5M albums, 54.3M tracks
  - SoundCloud: 3.08M artists, 2.8M albums, 10.3M tracks
- **Download**: Direct download from GitHub releases

### 2. OMDB (Openmusic Database)
- **Source**: https://github.com/OatsCG/OMDB
- **Size**: World's largest openly downloadable music database
- **License**: Unknown (check repository)
- **Format**: Database (tar format)
- **Features**: 
  - Full DB: 154M songs, 28M albums, 5.8M artists (80.3GB packed → 172GB unpacked)
  - Lite DB: Filtered version (11GB packed → 27GB unpacked)
- **Download**: 
  - Full: https://utoronto-my.sharepoint.com/:u:/g/personal/charlie_giannis_mail_utoronto_ca/IQCb_Ylbq7IfT5AvGvtFpF49AbvzdR7bEjiQqHYr5qjzR4w?e=kfzlrE
  - Lite: https://utoronto-my.sharepoint.com/:u:/g/personal/charlie_giannis_mail_utoronto_ca/IQAEnJBT0HH0QIfKoNwbpqhaAYPOPK6VO2t6RoFhD2BrLgc?e=ukFZMp

### 3. Discogs-VI Dataset
- **Source**: https://github.com/MTG/discogs-vi-dataset/
- **Size**: Large version identification dataset
- **License**: Unknown (check repository)
- **Format**: JSONL, JSON
- **Features**: Musical version metadata, YouTube mappings, artist metadata
- **Download**: 
  - Main: 1.4GB compressed → 21GB uncompressed
  - Intermediary: 8.7GB compressed → 46GB uncompressed
  - From Zenodo: https://doi.org/10.5281/zenodo.13983028

### 4. MusicOSet (Already Using)
- **Source**: https://github.com/marianaossilva/DSW2019
- **Size**: Enhanced music dataset
- **License**: Unknown (check repository)
- **Format**: SQL (233MB), CSV (69.5MB total)
- **Features**: 
  - musicoset_metadata.zip (5.73MB): Textual and numeric information
  - musicoset_popularity.zip (11.8MB): Popularity information
  - musicoset_songfeatures.zip (52MB): Lyrics and acoustic fingerprints
- **Download**: 
  - SQL: https://drive.google.com/open?id=1PXmpTLuDA40Ox8uHM7R2-s7UYH4hwbzx
  - CSVs: https://github.com/marianaossilva/DSW2019/blob/master/docs/assets/data/

## UCI Machine Learning Repository

### 1. FMA: A Dataset For Music Analysis
- **Source**: https://archive.ics.uci.edu/dataset/386/fma%2Ba%2Bdataset%2Bfor%2Bmusic%2Banalysis
- **Size**: 106,574 tracks
- **License**: Unknown (check dataset page)
- **Format**: Audio files + features
- **Features**: Song title, album, artist, genres; play counts, favorites, comments; audio features (518 attributes)
- **Download**: From UCI repository

### 2. Turkish Music Emotion
- **Source**: https://archive.ics.uci.edu/dataset/862/turkish+music+emotion
- **Size**: 400 samples (100 per emotion class)
- **License**: Unknown (check dataset page)
- **Format**: Audio features
- **Features**: MFCCs, Tempo, Chromagram, Spectral features; emotions: happy, sad, angry, relax
- **Download**: From UCI repository

### 3. Geographical Origin of Music
- **Source**: https://archive.ics.uci.edu/dataset/315/geographical+original+of+music
- **Size**: 1,059 tracks
- **License**: Unknown (check dataset page)
- **Format**: Text files
- **Features**: Audio features (68 or 116 attributes), geographical origin (latitude/longitude)
- **Download**: From UCI repository

### 4. YearPredictionMSD
- **Source**: http://archive.ics.uci.edu/dataset/203/yearpredictionmsd
- **Size**: 515,345 examples
- **License**: Unknown (check dataset page)
- **Format**: Text file
- **Features**: 90 audio features, year prediction (1922-2011)
- **Download**: From UCI repository

### 5. Bach Choral Harmony
- **Source**: https://archive.ics.uci.edu/dataset/298/bach+choral+harmony
- **Size**: 5,665 events from 60 chorales
- **License**: Unknown (check dataset page)
- **Format**: Data file + names file
- **Features**: Pitch classes, bass, meter, chord labels
- **Download**: From UCI repository

## MetaBrainz Foundation (MusicBrainz)

### 1. MusicBrainz PostgreSQL Data Dumps
- **Source**: https://metabrainz.org/datasets/postgres-dumps
- **Size**: ~20GB in postgres, 11.7GB in CSV format
- **License**: CC0 (core data), CC BY-NC-SA 3.0 (supplementary data)
- **Format**: XZ compressed PostgreSQL dumps
- **Update Frequency**: Twice weekly (Wednesdays and Saturdays)
- **Features**: Complete MusicBrainz database - artists, releases, labels, relationships, change history, ratings, tags
- **Download**: https://data.metabrainz.org/pub/musicbrainz/data/fullexport/

### 2. MusicBrainz JSON Data Dumps
- **Source**: https://metabrainz.org/datasets/postgres-dumps
- **Size**: Similar to PostgreSQL dumps
- **License**: CC0 (Public Domain)
- **Format**: XZ compressed JSONL (one JSON document per line)
- **Update Frequency**: Twice weekly (Saturdays and Wednesdays)
- **Features**: Individual dump files for Area, Artist, Event, Instrument, Label, Place, Recording, Release Group, Release, Series, Work
- **Download**: https://data.metabrainz.org/pub/musicbrainz/data/json-dumps/

### 3. ListenBrainz Data Dumps
- **Source**: https://metabrainz.org/datasets
- **Size**: Large listening history dataset
- **License**: CC0
- **Format**: PostgreSQL dumps
- **Features**: User listening information indexed against MusicBrainz
- **Download**: https://data.metabrainz.org/pub/musicbrainz/listenbrainz/fullexport/

## Additional Sources

### 1. Last.fm Dataset
- **Source**: Available through various Kaggle datasets
- **Features**: Artist popularity, scrobbles, listeners, tags
- **License**: Varies by dataset

### 2. Discogs Official Data Dumps
- **Source**: https://www.discogs.com/data/
- **Size**: Very large (millions of releases)
- **License**: CC0 1.0
- **Format**: XML/JSON
- **Features**: Complete Discogs database - releases, artists, labels, marketplace data

## Recommended Datasets for Festival Intelligence Terminal

### Best for Quick Integration:
1. **MusicOSet** (already integrated) - 11,518 artists, CSV format
2. **Music Artists Popularity (Kaggle)** - 1.4M artists, popularity metrics
3. **Electronic Music Knowledge (Hugging Face)** - 1.4M artists, easy API access

### Best for Comprehensive Coverage:
1. **OMDB** - 5.8M artists, largest freely available
2. **MusicMoveArr/Datasets** - Multi-platform, 2.7M MusicBrainz + 1.2M Spotify artists
3. **MusicBrainz Official Dumps** - Complete database, regular updates

### Best for Specific Features:
1. **Spotify Artist Collaboration Network** - Artist relationships/features
2. **Discogs Data Dumps** - Complete release/label information
3. **FMA Dataset** - Audio features + metadata

## License Summary

- **CC0 (Public Domain)**: MusicBrainz, Discogs, Electronic Music Knowledge - Most permissive, can use commercially
- **CC BY 4.0**: Spotify Collaboration Network - Attribution required, commercial use allowed
- **CC BY-SA 4.0**: Music Artists Popularity - Attribution + share-alike required
- **CC BY-NC 4.0**: Embeat Spotify Tracks - Non-commercial only
- **MIT**: Spotify Artists Dataset - Permissive, commercial use allowed

## Integration Recommendations

1. **Start with MusicOSet** (already integrated)
2. **Add Music Artists Popularity** for popularity metrics
3. **Consider Electronic Music Knowledge** for electronic music coverage
4. **Use MusicBrainz JSON dumps** for comprehensive artist metadata
5. **OMDB** for maximum artist coverage if storage allows

## Data Quality Notes

- MusicBrainz: Community-curated, high quality, comprehensive
- Spotify: Official API data, current, but limited to Spotify catalog
- Discogs: Community-curated, comprehensive for physical releases
- Last.fm: User-generated popularity data, good for trends
- OMDB: Aggregated from multiple sources, verify quality for specific use cases

---

# Festival-Specific Datasets

## Spotify Datasets for Festival Intelligence

### 1. Spotify Songs and Artists Dataset
- **Source**: https://www.kaggle.com/datasets/glowstudygram/spotify-songs-and-artists-dataset
- **Size**: Comprehensive Spotify catalog
- **License**: Unknown (check dataset page)
- **Format**: CSV
- **Features**: 
  - Artist Info: Name, Genres, Followers, Popularity (0-100), Spotify URL
  - Track Details: Track Name, Album Name, Release Date, Duration, Explicit Flag, Track Popularity
  - Audio Features: Danceability, Energy, Key, Loudness, Mode, Speechiness, Acousticness, Instrumentalness, Liveness, Valence, Tempo
- **Relevance**: **HIGH** - Perfect for artist momentum tracking, popularity metrics, and audio feature analysis
- **Download**: Direct CSV download from Kaggle

### 2. Spotify Most Streamed Artists of All Time
- **Source**: https://www.kaggle.com/datasets/meeratif/spotify-most-streamed-artists-of-all-time
- **Size**: Top streaming artists globally
- **License**: Unknown (check dataset page)
- **Format**: CSV
- **Features**: 
  - Artist name
  - Total streams (cumulative)
  - Daily average streams
  - Streams as lead artist
  - Solo streams
  - Featured streams
- **Relevance**: **HIGH** - Excellent for identifying top-tier artists for festival headliners
- **Download**: Direct CSV download from Kaggle

### 3. Spotify's Most Streamed Songs 2024-2026
- **Source**: https://www.kaggle.com/datasets/asmonline/spotify-song-performance-dataset
- **Size**: Current trending songs
- **License**: Unknown (check dataset page)
- **Format**: CSV
- **Features**: 
  - Songs & Artist names
  - Total streams
  - Daily streams (24h)
- **Relevance**: **HIGH** - Real-time trending data for booking decisions
- **Download**: Direct CSV download from Kaggle

### 4. Spotify Artists and Tracks Datasets
- **Source**: https://www.kaggle.com/datasets/gokulraja84/spotify-artists-and-tracks-datasets
- **Size**: 173.34 MB total
- **License**: Unknown (check dataset page)
- **Format**: CSV files
- **Features**: artists.csv (61.97 MB), tracks.csv with popularity metrics
- **Relevance**: **HIGH** - Clean Spotify data with popularity scores
- **Download**: Direct CSV download from Kaggle

### 5. Spotify Artist Feature Collaboration Network
- **Source**: https://www.kaggle.com/datasets/jfreyberg/spotify-artist-feature-collaboration-network
- **Size**: ~20k chart artists + 136k feature artists
- **License**: CC BY 4.0
- **Format**: Network dataset
- **Features**: Artist collaboration network with 135k+ musicians and 300k+ collaboration edges
- **Relevance**: **MEDIUM** - Useful for understanding artist relationships and co-billing opportunities
- **Download**: Direct download from Kaggle

## Festival Lineup & Historical Data

### 1. Glastonbury Lineup Scraper
- **Source**: https://github.com/jonty/glastoscrape
- **Size**: 2015-present lineups
- **License**: Unknown (check repository)
- **Format**: CSV
- **Features**: 
  - glastonbury_2022_schedule.csv (3,502 performances)
  - glastonbury_2022_schedule_onlymusic.csv (1,820 music performances)
  - glastonbury_2022_schedule_filtered.csv (Last.fm validated)
- **Relevance**: **HIGH** - Historical festival lineup data for trend analysis
- **Download**: Direct CSV download from GitHub

### 2. Tomorrowland Lineup Database
- **Source**: https://festivalviewer.com/tomorrowland/lineup/home
- **Size**: 9,600+ sets (2005-2025)
- **License**: Unknown (check website)
- **Format**: Web database (can be scraped)
- **Features**: 
  - Artist, Stage, Host, Timeslot, Date, Year, Day, Weekend, Genre
  - 20 years of comprehensive lineup data
- **Relevance**: **HIGH** - Major festival historical data for booking patterns
- **Download**: Web scraping or API access

### 3. Jazz Fest Database (New Orleans)
- **Source**: https://jfdb.jazzandheritage.org/
- **Size**: Complete history since 1970
- **License**: Unknown (check website)
- **Format**: Web database
- **Features**: 
  - Every performer at New Orleans Jazz & Heritage Festival
  - Name, Date, Venue, Time, Notes
  - 50+ years of festival data
- **Relevance**: **HIGH** - Long-term festival history for trend analysis
- **Download**: Web access or scraping

### 4. Montreux Jazz Festival Metadata
- **Source**: https://opendata.swiss/en/dataset/metadata-montreux-jazz-festival
- **Size**: All concerts 1967-present
- **License**: Open data license
- **Format**: JSON, XML
- **Features**: 
  - Complete concert metadata
  - Temporal coverage: January 1, 1967 to present
- **Relevance**: **HIGH** - Historical jazz festival data
- **Download**: Direct download from opendata.swiss

### 5. LineupRadar (Multi-Festival Tracker)
- **Source**: https://github.com/frankvaneykelen/lineup-radar
- **Size**: Multiple festivals, multiple years
- **License**: Unknown (check repository)
- **Format**: CSV per festival/year
- **Features**: 
  - Artist, Genre, Country, Bio, Website
  - AI Summary, AI Rating (1-10), Spotify Link
  - Band size, Gender of front person, Front person of color, Cancelled status
  - Festivals: Pinkpop, Down the Rabbit Hole, Rock Werchter, Footprints, Best Kept Secret
- **Relevance**: **VERY HIGH** - Comprehensive festival lineup data with AI enrichment
- **Download**: CSV files from GitHub repository

### 6. Glastonbury Festival API
- **Source**: https://parse.bot/marketplace/1ce688c5-900d-41d7-be5f-f40aaf6ac77a/glastonburyfestivals-co-uk-api
- **Size**: Historical and current lineups
- **License**: API access (check pricing)
- **Format**: JSON API
- **Features**: 
  - get_lineup_years: Available historical years
  - get_lineup: Full lineup for given year (artist, stage, day, set times)
  - get_lineup_by_stage: Lineup organized by stage
  - search_artist: Find performer across all years
  - get_news: Festival news and announcements
- **Relevance**: **HIGH** - Programmatic access to Glastonbury data
- **Download**: API access

## Concert & Event Datasets

### 1. JamBase Data
- **Source**: https://data.jambase.com/data
- **Size**: 616K+ artists, global event coverage
- **License**: Commercial API (check pricing)
- **Format**: JSON API
- **Features**: 
  - Concert & festival schedules with real-time updates
  - Day-by-day festival schedules and billing orders
  - Ticket pricing tracking
  - Venue data with GPS coordinates and capacity
  - Artist metadata with external IDs (Spotify, MusicBrainz, Ticketmaster)
  - Touring history (25 years on Pro+)
- **Relevance**: **VERY HIGH** - Comprehensive live event data perfect for festival intelligence
- **Download**: API access (free trial available)

### 2. Bandsintown Scraper (DataFlirt)
- **Source**: https://dataflirt.com/scraper/bandsintown/
- **Size**: 314K events/day, 89K per run
- **License**: Commercial scraping service
- **Format**: JSON, CSV, Parquet
- **Features**: 
  - Event ID, Artist Name, Event Date, Event Time
  - Venue Name, Venue Location, Ticket URL, Ticket Status
  - Lineup, Event Type, Festival Name
  - Venue Data: Capacity, GPS coordinates, upcoming events count
- **Relevance**: **HIGH** - Real-time tour schedule data
- **Download**: Commercial scraping service

### 3. Bandsintown Scraper (Apify)
- **Source**: https://apify.com/solidcode/bandsintown-scraper/api/openapi
- **Size**: Scalable scraping
- **License**: Commercial API ($1/1K records)
- **Format**: JSON, CSV
- **Features**: 
  - Upcoming, past, or all events with custom date ranges
  - Venue coordinates, full lineup, structured ticket offers
  - On-sale times and follower counts
  - Two record types: event rows + artist profiles
- **Relevance**: **HIGH** - Comprehensive tour data with venue details
- **Download**: API access

### 4. Concert Events Scraper (Songkick)
- **Source**: https://apify.com/oriented_wallpaper/concert-events-scraper/api/mcp
- **Size**: Artist-centric tour data
- **License**: Commercial API
- **Format**: CSV, JSON, Excel
- **Features**: 
  - Artist name, event name, ISO date/time
  - Venue, city, region, country, coordinates
  - Genres, festival flag, event status
  - Ticket link and Songkick URL
- **Relevance**: **MEDIUM** - Good for artist tour tracking
- **Download**: API access

## Festival Attendance & Revenue Data

### 1. Concert Box Office Dataset
- **Source**: https://www.selectdataset.com/dataset/0f7725014738b91b11f3018a75ac6c38/concert-box-office
- **Size**: 12,000+ venues
- **License**: Commercial dataset
- **Format**: Unknown (check provider)
- **Features**: 
  - Box office revenue (total USD from ticket sales)
  - Total tickets sold
  - Granularity: Entertainer, Venue, and Genre level
  - Coverage: Concerts, Festivals, Live Comedy, Live Theater
  - Bias: Skews toward US
- **Relevance**: **VERY HIGH** - Actual revenue and attendance data for festivals
- **Download**: Commercial dataset purchase

### 2. Pollstar Year-End Festival Grosses
- **Source**: https://data.pollstar.com/Chart/2024/01/121123_ye.top20.festival%20grosses_digital_1039.pdf
- **Size**: Top 20 festivals worldwide
- **License**: Commercial data
- **Format**: PDF report
- **Features**: 
  - Festival name, date, headliner
  - Tickets sold, gross revenue
  - Capacity, sell-out percentage
  - Ticket price ranges
- **Relevance**: **HIGH** - Industry-standard festival revenue data
- **Download**: PDF download from Pollstar

### 3. SeatData Event Ticket Sales Dataset
- **Source**: https://seatdata.io/datasets/event-ticket-sales/
- **Size**: 1M+ events since 2021, 70K+ current events
- **License**: Commercial dataset
- **Format**: CSV
- **Features**: 
  - Complete historical sales transaction records
  - Pricing and inventory details
  - Timestamp, event ID, listing ID, quantity, price
  - Zone, section, row information
- **Relevance**: **HIGH** - Detailed ticket sales data for demand analysis
- **Download**: Commercial dataset purchase

### 4. Ticketmaster Dataset (Rebrowser)
- **Source**: https://rebrowser.net/products/datasets/ticketmaster
- **Size**: Millions of events
- **License**: Commercial scraping service
- **Format**: Structured datasets
- **Features**: 
  - Event metadata, pricing histories
  - Venue information and attendance patterns
  - Presale timing and availability
  - Dynamic pricing patterns
- **Relevance**: **HIGH** - Comprehensive ticketing platform data
- **Download**: Commercial scraping service

## Setlist & Performance Data

### 1. setlist.fm API
- **Source**: https://api.setlist.fm/docs/1.0/index.html
- **Size**: World's largest crowd-sourced setlist database
- **License**: Free for non-commercial use, commercial requires contact
- **Format**: JSON API
- **Features**: 
  - Artist setlists with songs, venues, tour names
  - Historical setlist versions
  - Search by artist, venue, city, date, tour
  - Venue and city data with coordinates
- **Relevance**: **HIGH** - Performance data for artist set analysis
- **Download**: API access (requires API key)

### 2. setlist.fm Python Client
- **Source**: https://setlist-fm-client.readthedocs.io/en/latest/api/
- **Size**: Full API coverage
- **License**: Same as setlist.fm API
- **Format**: Python library
- **Features**: 
  - get_artist_setlists: Artist's complete setlist history
  - search_setlists: Search by various parameters
  - get_setlists_of_concerts_attended_by_user: User concert history
- **Relevance**: **HIGH** - Easy programmatic access to setlist data
- **Download**: pip install setlist-fm-client

## Festival Intelligence Terminal Recommendations

### **Most Valuable for Festival Intelligence:**

1. **JamBase Data** - Comprehensive live event data with festival schedules, pricing, venue info
2. **Concert Box Office Dataset** - Actual revenue and attendance data for festivals
3. **Spotify Songs and Artists Dataset** - Artist popularity, followers, audio features
4. **LineupRadar** - Multi-festival lineup data with AI enrichment
5. **setlist.fm API** - Performance data for artist set analysis

### **Quick Wins for Integration:**

1. **Spotify Most Streamed Artists** - Identify top-tier headliners
2. **Glastonbury Lineup Scraper** - Historical festival data for trend analysis
3. **Tomorrowland Lineup Database** - Major festival booking patterns
4. **Jazz Fest Database** - Long-term festival history

### **Commercial Data Worth Paying For:**

1. **JamBase Data** - Most comprehensive live event platform
2. **Concert Box Office Dataset** - Actual festival revenue data
3. **SeatData** - Detailed ticket sales analytics
4. **Pollstar Data** - Industry-standard festival grosses

### **Free Alternatives:**

1. **setlist.fm API** - Free for non-commercial use
2. **GitHub festival scrapers** - Glastonbury, LineupRadar
3. **Spotify Kaggle datasets** - Artist popularity and streaming data
4. **MusicBrainz** - Comprehensive artist metadata

---

# Social Media Sentiment & Artist Analytics Datasets

## Social Media Sentiment Datasets

### 1. Grammy Artists Instagram Dataset
- **Source**: https://doi.org/10.5281/zenodo.18965670
- **Size**: 309 Grammy-awarded artists, 34,811 posts (Feb 2023 - Feb 2024)
- **License**: Open data license (check Zenodo)
- **Format**: CSV
- **Features**: 
  - Post time, user (anonymized), gender (F/M/MIXED)
  - Followers, awards count, day difference
  - Video indicator, likes, comments
  - Carousel indicator, post images, caption length
  - Hashtag count, publication weekday
  - IM_performance (Frontstage/Backstage/Offstage)
- **Relevance**: **HIGH** - High-quality Instagram engagement data for award-winning artists
- **Download**: Direct download from Zenodo

### 2. Bad Bunny Social Media Dataset
- **Source**: https://www.datashake.com/datasets/bad-bunny-social-media-dataset
- **Size**: 2.1M social media mentions across 8 platforms
- **License**: Commercial dataset
- **Format**: JSON
- **Features**: 
  - Platform-specific content (TikTok, Twitter, Instagram, etc.)
  - Engagement metrics (likes, comments, shares, bookmarks, plays)
  - Text snippets, hashtags, author metadata
  - Media types (video, images)
- **Relevance**: **MEDIUM** - Single-artist case study, good for methodology
- **Download**: Commercial purchase from DataShake

### 3. Dixie D'Amelio Twitter Dataset
- **Source**: https://baselight.app/u/kaggle/dataset/thedevastator_social_interaction_analytics_of_dixie_d_amelio_s
- **Size**: 4,817 tweets with sentiment analysis
- **License**: Unknown (check dataset page)
- **Format**: CSV
- **Features**: 
  - Tweet text, sentiment scores
  - Like count, retweet count, quote counts
  - Conversation threads
- **Relevance**: **LOW** - Single influencer case study, not music-specific
- **Download**: Direct download from Baselight

### 4. Music Artist Controversy Detection Dataset
- **Source**: http://resolver.obvsg.at/urn:nbn:at:at-ubl:1-19181
- **Size**: Twitter data for music artists
- **License**: Academic research license
- **Format**: Research dataset
- **Features**: 
  - Twitter mentions of music artists
  - Controversy detection labels
  - User information and opinion data
  - Machine learning features for prediction
- **Relevance**: **HIGH** - Academic dataset for controversy detection in music domain
- **Download**: Academic repository access

### 5. Generation Z Music Sentiment Dataset
- **Source**: https://doi.org/10.62762/tsel.2025.125300
- **Size**: 500 digital comments (250 YouTube, 125 Twitter, 125 Instagram)
- **License**: Academic research license
- **Format**: Research dataset
- **Features**: 
  - Sentiment polarity (positive, negative, neutral)
  - Specific emotions (joy, sadness, anger, surprise, trust, anticipation, disgust, fear)
  - Platform source (YouTube, Twitter, Instagram)
  - Gender-balanced sample (50 men, 50 women)
  - Age range: 15-30 (Generation Z)
- **Relevance**: **HIGH** - Emotion detection for music comments across platforms
- **Download**: Academic repository access

## Social Media APIs for Sentiment Analysis

### 1. Recoup API
- **Source**: https://developers.recoupable.com/
- **Size**: 30+ endpoints for artist research
- **License**: Commercial API (check pricing)
- **Format**: REST API
- **Features**: 
  - Artist search, lookup, profile, metrics, audience
  - Social media scraping (Spotify, Instagram, X/Twitter)
  - Instagram posts and comments
  - X/Twitter search and trends
  - Festival data, web presence, career history
- **Relevance**: **VERY HIGH** - Comprehensive artist analytics with social media integration
- **Download**: API access (commercial)

### 2. ArtistPulse (GitHub)
- **Source**: https://github.com/kritinkaul/artistpulse
- **Size**: Real-time analytics dashboard
- **License**: Open source (check repository)
- **Format**: Full-stack application
- **Features**: 
  - Spotify integration (streaming metrics, popularity)
  - YouTube analytics (engagement, subscriber growth)
  - Last.fm insights (listening behavior, genre)
  - Twitter sentiment analysis
  - Reddit community discussions
  - Geographic analysis, competitive insights
- **Relevance**: **HIGH** - Open-source solution for multi-platform artist analytics
- **Download**: GitHub repository

### 3. Ionosphere (GitHub)
- **Source**: https://github.com/flaviovargasbrandao/Ionosphere
- **Size**: Social media impact prediction project
- **License**: Open source (check repository)
- **Format**: Python project
- **Features**: 
  - Social media post data collection (Instagram, Twitter/X, YouTube)
  - Spotify play and popularity metrics
  - Temporal correlation analysis
  - Predictive models for social media impact
  - Dashboard visualization
- **Relevance**: **HIGH** - Predictive analysis of social media impact on streaming
- **Download**: GitHub repository

### 4. ArtistViz (GitHub)
- **Source**: https://github.com/averatec0773/ArtistViz
- **Size**: Emotional trend visualization tool
- **License**: Open source (check repository)
- **Format**: Python project
- **Features**: 
  - Spotify and Genius API integration
  - Emotion detection (DistilBERT)
  - Topic modeling (LDA)
  - Sentiment analysis (VADER)
  - Lyrical pattern analysis
  - Mood distribution visualization
- **Relevance**: **MEDIUM** - Focuses on lyrics rather than social media sentiment
- **Download**: GitHub repository

### 5. PillowStruck (GitHub)
- **Source**: https://github.com/petitmi/PillowStruck
- **Size**: API wrapper for artist analysis
- **License**: Open source (check repository)
- **Format**: Python project with web interface
- **Features**: 
  - Spotify API integration
  - Musixmatch web crawling for lyrics
  - Word cloud generation
  - Sentiment analysis (tweetnlp)
  - Artist activity analysis
  - Track and album details
- **Relevance**: **MEDIUM** - Lyrics-focused with some social media capabilities
- **Download**: GitHub repository

## Festival Booking Criteria Research Summary

Based on industry research from major festivals (Bonnaroo, Primavera Sound, Osheaga, Sziget, Meredith), the key factors for festival booking decisions are:

### **Headliner Selection Criteria:**

1. **Commercial Viability**
   - Ticket-selling power and proven draw
   - Stadium/arena tour capability vs. festival availability
   - Global demand coherence (not just streaming popularity)
   - Scarcity value (not over-saturated in local market)

2. **Timing and Career Stage**
   - Two types of headliners: Long-career legacy acts vs. Hot emerging artists
   - Artists "exploding" and becoming very hot at the moment
   - Career trajectory and momentum
   - Album release cycles and touring schedules

3. **Festival Fit and Atmosphere**
   - Alignment with festival's "vibe" and brand identity
   - Ability to connect with specific festival environment
   - Cultural fit with festival audience demographics
   - Willingness to take creative risks

4. **Data-Driven Metrics**
   - Streaming numbers (Spotify, Apple Music)
   - Social media engagement and following
   - Ticket sales from recent tours
   - Radio airplay and chart performance
   - Fan feedback and surveys

5. **Intuition and Expert Networks**
   - Personal taste and experience of bookers
   - Recommendations from trusted sources (labels, colleagues)
   - Live performance assessment
   - Industry buzz and word-of-mouth

### **Supporting Act Selection Criteria:**

1. **Emerging Talent Discovery**
   - Artists with buzz but not yet mainstream
   - Critical acclaim (reviews, "best of" lists)
   - Subcultural relevance and scene leadership
   - Potential for breakout moments

2. **Lineup Cohesion**
   - Genre diversity while maintaining connective tissue
   - Stage-specific programming (different vibes per stage)
   - Time slot energy flow and scheduling
   - Geographic and demographic representation

3. **Risk and Opportunity**
   - Booking artists 12-18 months before breakthrough
   - Willingness to move artists up the bill as they grow
   - Local artist development and emergency backup acts
   - Balance of known vs. unknown quantities

### **Economic Considerations:**

1. **Financial Structure**
   - Headliners drive disproportionate early ticket sales
   - Secondary budget caps based on headliner costs
   - Risk mitigation through proven draws
   - ROI calculations for each booking tier

2. **Market Dynamics**
   - Competition for major talent across festivals
   - Stadium tour economics vs. festival fees
   - Local market saturation and routing efficiency
   - Sponsor confidence based on headliner quality

### **Modern Challenges:**

1. **Headliner Shortage**
   - Limited pool of artists capable of headlining 80K+ capacity
   - Stadium tours offering higher fees than festivals
   - Post-COVID shift in touring economics
   - Need for creative solutions and smaller headliners

2. **Social Media Impact**
   - TikTok enabling rapid artist ascents (Chappell Roan, Charli XCX)
   - Streaming not equal to mobilization (willingness to travel/pay)
   - Algorithm-driven discovery vs. tastemaker curation
   - Real-time sentiment monitoring for booking decisions

## Festival Intelligence Terminal Integration Recommendations

### **Social Media Sentiment Integration:**

1. **Primary Data Sources:**
   - **Recoup API** - Comprehensive social media scraping and sentiment
   - **ArtistPulse** - Open-source multi-platform analytics
   - **Grammy Instagram Dataset** - High-quality engagement benchmarks

2. **Sentiment Metrics to Track:**
   - Social media follower growth rate
   - Engagement rate (likes/comments per follower)
   - Sentiment polarity (positive/negative/neutral)
   - Emotion detection (joy, excitement, anticipation)
   - Controversy detection and risk assessment
   - Cross-platform consistency (Spotify vs. social media)

3. **Booking Decision Framework:**
   - **Headliner Score:** 40% commercial viability + 30% timing + 20% festival fit + 10% sentiment
   - **Supporting Act Score:** 30% buzz/momentum + 25% critical acclaim + 25% lineup fit + 20% sentiment
   - **Risk Assessment:** Controversy detection, over-saturation analysis, demographic mismatch

4. **Real-Time Monitoring:**
   - Social media sentiment trends (30-day, 90-day windows)
   - Streaming velocity changes
   - Tour announcement impact on sentiment
   - Festival announcement reaction analysis

---

# Monid.ai Integration

## Overview

Monid.ai is an AI agent tool integration platform that provides access to 1,300+ tools and APIs through a single integration. This can significantly accelerate the Festival Intelligence Terminal's data collection capabilities by eliminating the need to implement individual API integrations for each data source.

## Key Benefits for Festival Intelligence Terminal

### 1. **Unified API Access**
- Single integration point for 1,300+ tools
- No per-tool sign-ups or subscriptions
- One balance to use all tools
- Automatic tool discovery and comparison

### 2. **Cost Efficiency**
- Pay-per-call or per-result pricing
- No subscription fees
- Transparent pricing model
- Start with $1 in free credit

### 3. **Agent-First Design**
- Built specifically for AI agents
- Runtime tool discovery
- Automatic tool selection
- MCP (Model Context Protocol) support

### 4. **Multiple Integration Methods**
- **Skill**: One line integration into agent chat
- **MCP**: Remote MCP server addition
- **CLI**: Terminal-based tool access
- **HTTP API**: Direct API calls

## Potential Tools for Festival Intelligence

Based on the 1,300+ available tools, Monid.ai could provide access to:

### Social Media APIs
- Twitter/X data scraping
- Instagram analytics
- TikTok metrics
- Reddit data
- Facebook insights

### Music Platform APIs
- Spotify Web API (already using, but could be unified)
- Apple Music API
- YouTube Music API
- SoundCloud API
- Deezer API

### Event & Ticketing APIs
- Ticketmaster data
- Eventbrite integration
- SeatGeek data
- AXS ticketing
- Live Nation data

### Web Scraping Tools
- General web scraping
- Content extraction
- Data parsing
- URL monitoring

### Analytics & Metrics
- Social media analytics
- Web traffic data
- Search trends
- Brand monitoring

## Integration Approaches

### 1. MCP Integration (Recommended for AI Agents)
Add Monid as a remote MCP server to enable automatic tool discovery and execution by the Festival Intelligence Terminal's AI components.

### 2. CLI Integration
Use Monid CLI for manual data collection and testing:
```bash
monid discover "spotify artist data"
monid run --tool spotify --artist "artist name"
```

### 3. HTTP API Integration
Direct API calls for programmatic access to tools:
```python
import requests
response = requests.post('https://api.monid.ai/run', 
    json={'tool': 'spotify', 'query': 'artist name'})
```

### 4. Skill Integration
Add Monid skill to agent chat interface for natural language tool invocation.

## Implementation Strategy

### Phase 1: Evaluation
- Sign up for Monid.ai account
- Test with $1 free credit
- Identify relevant tools for festival intelligence
- Evaluate pricing for expected usage

### Phase 2: Pilot Integration
- Integrate MCP server for AI agent access
- Test with 3-5 key data sources
- Compare with existing manual integrations
- Assess data quality and reliability

### Phase 3: Production Integration
- Replace individual API integrations where beneficial
- Implement fallback mechanisms for critical tools
- Monitor usage and costs
- Optimize tool selection

### Phase 4: Expansion
- Add additional tools as needed
- Implement custom tool chaining
- Build specialized workflows
- Integrate with Scrapy for enhanced scraping

## Cost Considerations

### Pricing Model
- **Per-call**: Flat fee per execution (e.g., $0.003/call)
- **Per-result**: Base fee + per-item fee (e.g., $0.002 + $0.001/result)

### Cost Optimization
- Cache results to reduce repeated calls
- Batch requests where possible
- Use per-result pricing for large datasets
- Monitor usage patterns

### Comparison with Direct API Integration
- **Pros**: Single integration, no subscriptions, automatic updates
- **Cons**: Per-call costs may exceed direct API for high-volume usage
- **Recommendation**: Use for low-volume, high-variety tool access; direct API for high-volume, critical data sources

## Recommended Tools to Prioritize

Based on Festival Intelligence Terminal needs, prioritize these Monid tools:

1. **Social Media APIs** - Instagram, Twitter, TikTok for sentiment analysis
2. **Event APIs** - Ticketmaster, Eventbrite for concert/festival data
3. **Web Scraping** - Festival websites for lineup data
4. **Analytics** - Social media monitoring and trend analysis
5. **Music APIs** - Additional platforms beyond Spotify

## Documentation

- **Website**: https://monid.ai/
- **Tools Catalog**: https://monid.ai/tools
- **Documentation**: https://monid.ai/docs
- **Get Started**: https://app.monid.ai

## Integration with Existing Components

### Scrapy Integration
Monid can complement Scrapy by:
- Providing pre-built API integrations for data sources
- Handling authentication for complex APIs
- Offering alternative data collection methods
- Enabling rapid prototyping of new data sources

### Festival Intelligence Terminal Backend
- Use Monid MCP server for AI-driven tool selection
- Implement tool discovery for dynamic data collection
- Add Monid as a fallback for missing API integrations
- Enable natural language data requests

### Social Media Sentiment Analysis
- Access multiple social platforms through single integration
- Unified sentiment analysis across platforms
- Real-time monitoring capabilities
- Reduced authentication complexity

## Next Steps

1. **Sign up for Monid.ai account** - Get started with free credit
2. **Explore tools catalog** - Identify relevant tools for festival intelligence
3. **Test MCP integration** - Evaluate agent-driven tool discovery
4. **Pilot with key data sources** - Compare with existing implementations
5. **Assess cost-benefit** - Determine which tools to integrate vs. direct API
