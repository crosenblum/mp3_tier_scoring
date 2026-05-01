import sys
import re
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

from mutagen import File
from rapidfuzz import fuzz


# =========================================================
# PATH VALIDATION
# =========================================================

def validate_path(path: str) -> Path:
    """
    Validate and normalize input directory path.
    """

    # enforce correct input type early
    if not isinstance(path, str):
        raise ValueError("Path must be a string")

    if not path.strip():
        raise ValueError("Empty path provided")

    p = Path(path)

    if not p.exists():
        raise ValueError(f"Path does not exist: {path}")

    if not p.is_dir():
        raise ValueError(f"Path is not a directory: {path}")

    return p


def is_mp3(file: Path) -> bool:
    """Check if file is MP3 based on extension."""
    return isinstance(file, Path) and file.suffix.lower() == ".mp3"


# =========================================================
# FILENAME PARSING
# =========================================================

FILENAME_PATTERNS = [
    re.compile(r"^(?P<track>\d+)\s*-\s*(?P<title>.+)$"),
    re.compile(r"^(?P<artist>.+)\s*-\s*(?P<title>.+)$"),
]


def parse_filename(filename: str) -> Dict[str, Optional[str]]:
    """Extract metadata hints from filename."""
    if not isinstance(filename, str):
        return {"artist": None, "title": None, "track": None, "unparseable": True}

    stem = Path(filename).stem.strip()

    for pattern in FILENAME_PATTERNS:
        m = pattern.match(stem)
        if m:
            return {
                "artist": m.groupdict().get("artist"),
                "title": m.groupdict().get("title"),
                "track": m.groupdict().get("track"),
                "unparseable": False,
            }

    return {"artist": None, "title": None, "track": None, "unparseable": True}


# =========================================================
# NORMALIZATION
# =========================================================

def normalize(text: Optional[str]) -> str:
    """Normalize text for comparison."""
    if not text:
        return ""

    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text)


# =========================================================
# TRACK MODEL
# =========================================================

class TrackFeatures:
    """Container for MP3 metadata + derived analysis state."""

    def __init__(
        self,
        path: str,
        artist: Optional[str],
        title: Optional[str],
        album: Optional[str],
        tracknumber: Optional[int],
        bitrate: Optional[int],
        filename_title: Optional[str],
        corrupt: bool,
        match_state: str,
        failure_type: Optional[str] = None,   # NEW: Tier F subcategory
    ):
        self.path = path
        self.artist = artist
        self.title = title
        self.album = album
        self.tracknumber = tracknumber
        self.bitrate = bitrate
        self.filename_title = filename_title
        self.corrupt = corrupt
        self.match_state = match_state
        self.failure_type = failure_type      # NEW


# =========================================================
# FAILURE CLASSIFICATION (NEW CORE ADDITION)
# =========================================================

def classify_failure(tags: Optional[dict], audio: Optional[object]) -> str:
    """
    Determine Tier F failure category.

    This is what enables:
    - filtering
    - move operations
    - subcategory inspection
    """

    if audio is None:
        return "corrupt"

    if not tags:
        return "no_tags"

    return "parse_failure"


# =========================================================
# MATCH STATE
# =========================================================

def get_match_state(fn_title: Optional[str], tag_title: Optional[str]) -> str:
    """Compare filename vs metadata title."""

    fn = normalize(fn_title)
    tag = normalize(tag_title)

    if not fn or not tag:
        return "mismatch"

    if fn in tag or tag in fn:
        return "clean_match"

    score = fuzz.ratio(fn, tag)

    if score >= 90:
        return "clean_match"
    elif score >= 75:
        return "partial_match"
    return "mismatch"


# =========================================================
# FEATURE EXTRACTION
# =========================================================

def extract_features(file_path: Path) -> TrackFeatures:
    """Extract metadata from single file."""

    try:
        audio = File(file_path)

        if audio is None:
            raise ValueError("Unreadable file")

        tags = audio.tags or {}

        def safe(tag):
            try:
                return str(tag.text[0]) if tag and hasattr(tag, "text") else None
            except Exception:
                return None

        artist = safe(tags.get("TPE1"))
        title = safe(tags.get("TIT2"))
        album = safe(tags.get("TALB"))

        tracknumber = None
        try:
            tr = tags.get("TRCK")
            if tr:
                tracknumber = int(str(tr.text[0]).split("/")[0])
        except Exception:
            tracknumber = None

        fname = parse_filename(file_path.name)
        match_state = get_match_state(fname.get("title"), title)

        return TrackFeatures(
            path=str(file_path),
            artist=artist,
            title=title,
            album=album,
            tracknumber=tracknumber,
            bitrate=getattr(audio.info, "bitrate", None),
            filename_title=fname.get("title"),
            corrupt=False,
            match_state=match_state,
            failure_type=None,
        )

    except Exception:
        # classify failure explicitly for Tier F routing
        return TrackFeatures(
            path=str(file_path),
            artist=None,
            title=None,
            album=None,
            tracknumber=None,
            bitrate=None,
            filename_title=None,
            corrupt=True,
            match_state="mismatch",
            failure_type="corrupt",
        )


# =========================================================
# SCORING
# =========================================================

def score_track(t: TrackFeatures) -> Tuple[int, Dict[str, int]]:
    """Single-pass scoring (no recomputation elsewhere)."""

    score = 100

    defects = {
        "missing_artist": 0,
        "missing_title": 0,
        "missing_track": 0,
        "filename_mismatch": 0,
        "low_bitrate": 0,
        "corrupt": 0,
    }

    if t.corrupt:
        defects["corrupt"] = 1
        return 0, defects

    if not t.artist:
        score -= 25
        defects["missing_artist"] = 1

    if not t.title:
        score -= 25
        defects["missing_title"] = 1

    if not t.tracknumber:
        score -= 15
        defects["missing_track"] = 1

    if t.match_state != "clean_match":
        defects["filename_mismatch"] = 1

    if t.bitrate and t.bitrate < 128000:
        score -= 3
        defects["low_bitrate"] = 1

    return max(score, 0), defects


# =========================================================
# TIERING
# =========================================================

def tier(score: int) -> str:
    if score >= 90: return "A"
    if score >= 75: return "B"
    if score >= 60: return "C"
    if score >= 45: return "D"
    if score >= 30: return "E"
    return "F"


def apply_gate(tier_label: str, match_state: str) -> str:
    if tier_label == "A" and match_state != "clean_match":
        return "B"
    if match_state == "mismatch" and tier_label in ("A", "B"):
        return "C"
    return tier_label


# =========================================================
# FILE COLLECTION
# =========================================================

def collect_files(root: Path) -> List[Path]:
    return [p for p in root.rglob("*") if p.is_file() and is_mp3(p)]


# =========================================================
# ANALYSIS (THREADING)
# =========================================================

def analyze(files: List[Path], workers: int = 8):

    results = {t: [] for t in "ABCDE"}
    results["F"] = {}  # NEW: structured failure buckets

    defects = {t: {} for t in "ABCDEF"}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(extract_features, f) for f in files]

        for future in as_completed(futures):
            feat = future.result()

            score, d = score_track(feat)

            base = tier(score)
            final = apply_gate(base, feat.match_state)

            if final == "F":
                # ensure structured grouping
                bucket = feat.failure_type or "parse_failure"
                results["F"].setdefault(bucket, []).append(feat)
            else:
                results[final].append((feat, score))

            for k, v in d.items():
                defects[final][k] = defects[final].get(k, 0) + v

    return results, defects


# =========================================================
# SAFE MOVE SYSTEM (NEW CORE FUNCTIONALITY)
# =========================================================

def validate_move_paths(source_root: Path, dest_root: Path):
    """
    Ensure destination is safe and not inside scan tree.
    """

    source_root = source_root.resolve()
    dest_root = dest_root.resolve()

    # CRITICAL: prevent recursive ingestion loops
    if dest_root == source_root or source_root in dest_root.parents:
        raise ValueError("Destination cannot be inside scan root")

    # ensure writable target directory exists
    dest_root.mkdir(parents=True, exist_ok=True)

    return dest_root


def move_files(files: List[TrackFeatures], dest_root: Path, source_root: Path):
    """
    Move files safely across OS boundaries.
    """

    dest_root = validate_move_paths(source_root, dest_root)

    for f in files:
        src = Path(f.path)

        # preserve relative structure
        rel = src.relative_to(source_root)
        target = dest_root / rel

        target.parent.mkdir(parents=True, exist_ok=True)

        # collision handling
        if target.exists():
            target = target.with_stem(target.stem + "_dup")

        shutil.move(str(src), str(target))


# =========================================================
# REPORT
# =========================================================

def report(results, defects, tier_filter=None, list_mode=False):
    """
    Structured tier report renderer.

    ONLY CHANGE IN THIS VERSION:
    - Core health is omitted if empty
    - Main issues is omitted if empty

    No other behavior modified.
    """

    total = sum(len(v) for k, v in results.items() if k != "F")

    def safe_pct(n):
        return (n / total * 100) if total else 0

    def print_section(title: str, data: Dict[str, int]):
        """
        Print non-zero defect groups in sorted order.
        """
        items = sorted(data.items(), key=lambda x: -x[1])

        printed = False
        for k, v in items:
            if v > 0:
                if not printed:
                    print(f"\n{title}")
                    printed = True
                print(f"- {k}: {v}")

    for t in (tier_filter if tier_filter else "ABCDE"):
        items = results[t]
        if not items:
            continue

        count = len(items)
        avg = sum(score for _, score in items) / count

        print(f"\nTier {t} | count: {count} ({safe_pct(count):.1f}%) | avg: {avg:.1f}")

        if list_mode:
            for feat, _ in items:
                print("-", feat.path)
            continue

        d = defects[t]

        core = {
            "missing_artist": d.get("missing_artist", 0),
            "missing_title": d.get("missing_title", 0),
        }

        main = {
            "missing_track": d.get("missing_track", 0),
            "filename_mismatch": d.get("filename_mismatch", 0),
        }

        secondary = {
            "low_bitrate": d.get("low_bitrate", 0),
        }

        # Core health (ONLY print if non-empty after filtering)
        if any(v > 0 for v in core.values()):
            print_section("Core health", core)

        # Main issues (ONLY print if non-empty after filtering)
        if any(v > 0 for v in main.values()):
            print_section("Main issues", main)

        print_section("Secondary", secondary)
        print_section("Top fixes", d)

    if "F" in results and results["F"]:
        f = results["F"]

        # check if there is any actual data in any bucket
        has_data = any(len(v) > 0 for v in f.values())

        if not has_data:
            return  # or just skip Tier F section entirely

        print("\nTier F")

        for subtype, files in f.items():
            if not files:
                continue

            print(f"\n{subtype} | count: {len(files)}")

            if list_mode:
                for ft in files:
                    print("-", ft.path)

        print("\nTop fixes")
        agg = {k: len(v) for k, v in f.items()}
        for k, v in sorted(agg.items(), key=lambda x: -x[1]):
            print(f"- {k}: {v}")


# =========================================================
# MAIN (CLI EXTENSION HOOK READY)
# =========================================================

def main():
    """
    CLI entry point.

    USAGE (updated):

    python script.py <folder>
    python script.py <folder> --list
    python script.py <folder> --list F
    python script.py <folder> --list F corrupt

    BEHAVIOR:
    - No args: full report
    - --list: lists all files
    - --list F: lists all Tier F files grouped by subtype
    - --list F <subtype>: lists only that failure subtype (e.g. corrupt)
    """

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python script.py <folder>")
        print("  python script.py <folder> --list")
        print("  python script.py <folder> --list F")
        print("  python script.py <folder> --list F corrupt")
        sys.exit(1)

    folder = validate_path(sys.argv[1])

    # -----------------------------
    # CLI ARG PARSING
    # -----------------------------
    list_mode = "--list" in sys.argv

    tier_filter = None
    f_subtype_filter = None

    # capture positional CLI args after folder
    args = [a for a in sys.argv[2:] if a != "--list"]

    # detect Tier filter (A–F)
    for a in args:
        if a.upper() in ("A", "B", "C", "D", "E", "F"):
            tier_filter = a.upper()

    # detect Tier F subtype filter (corrupt / no_tags / parse_failure)
    for a in args:
        if a.lower() in ("corrupt", "no_tags", "parse_failure"):
            f_subtype_filter = a.lower()

    # -----------------------------
    # VALIDATION RULES
    # -----------------------------

    # list mode with no tier is allowed (lists everything)
    # but subtype only makes sense with F
    if f_subtype_filter and tier_filter != "F":
        print("Error: subtype filter only valid with Tier F")
        sys.exit(1)

    # -----------------------------
    # RUN PIPELINE
    # -----------------------------
    files = collect_files(folder)

    if not files:
        print("Error: no mp3 files found")
        sys.exit(1)

    results, defects = analyze(files)

    # -----------------------------
    # LIST MODE OVERRIDE
    # -----------------------------
    if list_mode:

        # CASE 1: Tier F subtype listing
        if tier_filter == "F" and f_subtype_filter:
            items = results["F"].get(f_subtype_filter, [])
            for ft in items:
                print("-", ft.path)
            return

        # CASE 2: Tier F full listing
        if tier_filter == "F":
            for subtype, items in results["F"].items():
                print(f"\n{subtype}")
                for ft in items:
                    print("-", ft.path)
            return

        # CASE 3: normal tier listing
        for t in (tier_filter if tier_filter else "ABCDE"):
            for feat, _ in results.get(t, []):
                print("-", feat.path)
        return

    # -----------------------------
    # NORMAL REPORT MODE
    # -----------------------------
    report(results, defects, tier_filter=tier_filter)


if __name__ == "__main__":
    main()