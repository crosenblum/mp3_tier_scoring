MP3 Tier Scoring Script

Overview:
This script scans a directory containing MP3 files and evaluates each file based on metadata quality, filename consistency, and basic audio properties. It assigns each file a tier rating from A to F, where A represents clean, well-structured files and F represents failed or unreadable files.

The goal is not perfection or metadata correction. The goal is large-scale analysis of MP3 library health so users can identify structural problems, missing tags, filename mismatches, and low-quality or corrupt files.

Core Functionality:

1. Directory Scanning
- Accepts a single folder path as input
- Validates that the path exists and is a directory
- Recursively scans all MP3 files in that directory tree

2. Metadata Extraction
- Uses mutagen to read MP3 tags
- Extracts artist, title, album, track number, and bitrate where available
- Handles missing or malformed metadata safely

3. Filename Analysis
- Parses filename structure to infer artist and title
- Compares filename-derived title with metadata title
- Detects mismatches and partial matches

4. Scoring System
- Each file starts at a base score of 100
- Points are deducted for missing or inconsistent metadata
- Factors include:
-- missing artist
-- missing title
-- missing track number
-- filename mismatch
-- low bitrate
-- unreadable or corrupt files

5. Tier Assignment
- A: high quality, clean metadata and strong filename match
- B: mostly clean with minor structural issues
- C: degraded metadata consistency
- D/E: progressively lower quality (depending on thresholds)
- F: failed, corrupt, or unreadable files

Failure Classification (Tier F):
Tier F is further divided into failure types:

- corrupt: file cannot be read
- no_tags: no usable metadata found
- parse_failure: metadata parsing failed

These are used for filtering, listing, and optional quarantine workflows.

Reporting Output:
The script prints a console-based report grouped by tier.

Each tier includes:

- file count
- percentage of total library
- average score

For tiers A–C:

Secondary issues (minor quality degradation factors)
Top fixes (most common problems affecting that tier)

Empty diagnostic sections are not displayed.

Tier F:

- grouped by failure type
- optionally lists files per category
- suppressed entirely if no failures exist

List Mode:
A list mode is available to output file paths instead of full analysis.

Supported modes:

- list all files by tier
- list Tier F grouped by failure type
- list only a specific Tier F subtype (for example corrupt)

Performance Characteristics:

- Uses multi-threaded processing for file scanning
- Optimized for medium to large libraries
- Designed for local disk analysis workloads
- Memory usage scales with number of scanned files

Recommended usage range:

- 1,000 to 50,000 MP3 files: optimal
- 50,000+ files: still usable but may require longer processing time

External Dependencies:

- mutagen (for MP3 metadata parsing)
- rapidfuzz (for filename vs metadata comparison)
- Python standard library modules (os, pathlib, concurrent.futures, etc.)

Design Philosophy:

- No destructive operations
- No automatic file modification
- No metadata rewriting
- Focus is strictly analysis and classification
- Any fixing of files is intended to be done externally using separate tools

Important Constraints:

- The script does not modify or delete files
- Tier scoring is informational only
- File movement or cleanup workflows are optional extensions, not core functionality

Usage:

Run the script with a folder path containing MP3 files. The script will scan all files and output tiered analysis results directly to the console.
