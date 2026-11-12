
#!/usr/bin/env python3
"""Download Suttara's DN 22 texts and optional spoken recordings.

Dependencies: pip install requests
Usage: python download_suttara_sources.py [--output DIR] [--skip-audio]

Creates clean UTF-8 plain-text Pāli and English files using SuttaCentral's
published Bilara editions. Spoken Pāli is read by Ānandajoti Bhikkhu;
spoken English follows Bhikkhu Sujato's translation and is read by
Roland Kitchen (Pali Audio). Audio is kept separate for licensing purposes.
Goenka's YouTube recording is referenced, never downloaded.

Important: existing .txt files are not regenerated, since Suttara sections may
refer to their line/column positions. Use --refresh-text only after making
backups and checking any section references.
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import unicodedata
import zipfile
from pathlib import Path

import requests

REPOSITORY = "https://raw.githubusercontent.com/suttacentral/bilara-data/published/"
TEXT_SOURCES = {
    "pali": {
        "url": REPOSITORY + "root/pli/ms/sutta/dn/dn22_root-pli-ms.json",
        "filename": "mahasatipatthana_pali.txt",
        "credit": "SuttaCentral, Mahāsaṅgīti Pāli edition, Dīgha Nikāya 22",
        "license": "CC0 (as described in SuttaCentral publication metadata)",
    },
    "english": {
        "url": REPOSITORY + "translation/en/sujato/sutta/dn/dn22_translation-en-sujato.json",
        "filename": "mahasatipatthana_english.txt",
        "credit": "Bhikkhu Sujato, The Longer Discourse on Mindfulness Meditation (DN 22)",
        "license": "CC0 1.0 — https://creativecommons.org/publicdomain/zero/1.0/",
    },
}
AUDIO_ARCHIVES = {
    "spoken_pali": "https://ancient-buddhist-texts.net/Audio/Zips/Pali-Mahasatipatthana.zip",
}

# Exact audio links published at https://www.paliaudio.com/dn22 .
# These recordings follow the Sujato translation, unlike the previous
# Ānandajoti English audio. Existing older recordings are not deleted.
ENGLISH_AUDIO = (
    {
        "url": "https://static1.squarespace.com/static/58384d438419c2f3cb695f5b/t/5e98671693b8617043b5c18a/1587627429237/DN%2B22%2B%281%29%2BThe%2Blonger%2Bdiscourse%2Bon%2Bmindfulness%2Bmeditation%281%29.mp3",
        "filename": "dn22_sujato_roland_kitchen_part1.mp3",
    },
    {
        "url": "https://static1.squarespace.com/static/58384d438419c2f3cb695f5b/t/5e98689a6c3087496beeb7db/1587627429241/DN%2B22%2B%282%29%2BThe%2Blonger%2Bdiscourse%2Bon%2Bmindfulness%2Bmeditation.mp3",
        "filename": "dn22_sujato_roland_kitchen_part2.mp3",
    },
)
ENGLISH_AUDIO_PAGE = "https://www.paliaudio.com/dn22"
GOENKA_VIDEO = "https://www.youtube.com/watch?v=BrRDmiw0jvA"


def download(session: requests.Session, url: str, destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size:
        print(f"  exists: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with session.get(url, timeout=(15, 120), stream=True) as response:
            response.raise_for_status()
            with temporary.open("wb") as file:
                for chunk in response.iter_content(chunk_size=128 * 1024):
                    if chunk:
                        file.write(chunk)
        temporary.replace(destination)
        print(f"  saved: {destination}")
    finally:
        temporary.unlink(missing_ok=True)


def is_heading(key: str) -> bool:
    """Bilara headings (e.g. dn22:0.1 or dn22:2.0.1)."""
    part = key.split(":", 1)[-1]
    return part.startswith("0.") or ".0." in part


def join_fragments(parts: list[str]) -> str:
    """Join adjacent Bilara fragments without inserting spurious spaces.

    Source segments typically end in whitespace, while punctuation can be
    deliberately attached to the next segment. Preserve that intent.
    """
    output = ""
    for part in parts:
        if not part:
            continue
        if not output:
            output = part
        elif output[-1].isspace() or part[0].isspace() or part[0] in ",.;:!?)]}”’":
            output += part
        elif output[-1] in "([‘“":
            output += part
        else:
            output += " " + part
    return re.sub(r"[ \t]+", " ", output).strip()


def format_bilara(data: dict[str, str]) -> str:
    """Turn ordered DN22 fragments into paragraphs without losing any text.

    Headings start their own paragraphs. Ordinary fragments are joined until
    a sentence finishes near the preferred paragraph length, or an explicit
    empty source segment appears. No semantic paraphrasing or rewriting.
    """
    if not isinstance(data, dict) or len(data) < 50:
        raise ValueError("Unexpectedly small or invalid DN 22 source")
    if not any(k.startswith("dn22:") for k in data):
        raise ValueError("Downloaded text does not contain DN 22 segments")

    paragraphs: list[str] = []
    pending: list[str] = []
    length = 0

    def flush() -> None:
        nonlocal pending, length
        if pending:
            joined = join_fragments(pending)
            if joined:
                paragraphs.append(joined)
        pending = []
        length = 0

    for key, value in data.items():  # JSON insertion order is the published reading order
        if not isinstance(key, str) or not key.startswith("dn22:") or not isinstance(value, str):
            raise ValueError(f"Unexpected source entry: {key!r}")
        value = unicodedata.normalize("NFC", value).replace("\r", " ").replace("\n", " ")
        if not value.strip():
            flush()
            continue
        if is_heading(key):
            flush()
            paragraphs.append(value.strip())
            continue
        pending.append(value)
        length += len(value)
        if length >= 500 and re.search(r"[.!?。！？][’”\"']?\s*$", value):
            flush()
    flush()
    return "\n\n".join(paragraphs).strip() + "\n"


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    try:
        part.write_text(content, encoding="utf-8", newline="\n")
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)


def get_text(session: requests.Session, output: Path, refresh: bool) -> dict:
    manifest = {}
    for language, info in TEXT_SOURCES.items():
        dest = output / "texts" / info["filename"]
        if dest.exists() and not refresh:
            print(f"Text {language}: existing file kept: {dest}")
        else:
            print(f"Text {language}: {info['url']}")
            response = session.get(info["url"], timeout=(15, 90))
            response.raise_for_status()
            source = response.json()
            content = format_bilara(source)
            # Do not replace a working copy with obviously incomplete content.
            if len(content) < 5000:
                raise ValueError(f"Generated {language} text is unexpectedly short")
            write_atomic(dest, content)
            print(f"  saved: {dest} ({len(content):,} characters)")
        if dest.is_file():
            manifest[language] = {
                "path": str(dest.relative_to(output)),
                "source_url": info["url"],
                "credit": info["credit"],
                "license": info["license"],
                "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
            }
    return manifest


def extract_audio(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zip_file:
        for info in zip_file.infolist():
            if info.is_dir() or not info.filename.lower().endswith(".mp3"):
                continue
            name = Path(info.filename).name
            if not name:
                continue
            destination = target / name
            if destination.exists() and destination.stat().st_size == info.file_size:
                continue
            temp = destination.with_suffix(".mp3.part")
            try:
                with zip_file.open(info) as src, temp.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                temp.replace(destination)
            finally:
                temp.unlink(missing_ok=True)
    print(f"  extracted: {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("suttara_sources"))
    parser.add_argument("--skip-audio", action="store_true")
    parser.add_argument("--skip-text", action="store_true")
    parser.add_argument("--refresh-text", action="store_true",
                        help="Overwrite existing text files; may invalidate saved section positions")
    args = parser.parse_args()
    if args.skip_text and args.refresh_text:
        parser.error("--skip-text and --refresh-text cannot be used together")
    out = args.output.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers["User-Agent"] = "SuttaraStudySourceDownloader/0.2 (personal study)"

    metadata_path = out / "sources.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
        if not isinstance(metadata, dict):
            raise ValueError("sources.json must contain a JSON object")
    except (ValueError, OSError) as error:
        print(f"ERROR reading existing {metadata_path}: {error}", file=sys.stderr)
        return 1

    try:
        if not args.skip_text:
            metadata["bundled_texts"] = get_text(session, out, args.refresh_text)
        metadata["title"] = "Mahāsatipaṭṭhāna Sutta"
        metadata["reference"] = "DN 22"
        metadata["audio_archives"] = AUDIO_ARCHIVES
        metadata["spoken_english_source"] = {
            "title": "The Longer Discourse on Mindfulness Meditation (DN 22)",
            "translation": "Bhikkhu Sujato (CC0)",
            "reader": "Roland Kitchen",
            "publisher": "Pali Audio",
            "page": ENGLISH_AUDIO_PAGE,
            "recording_license": "Not verified; do not assume permission to redistribute",
            "files": [
                {"path": f"audio/spoken_english/{item['filename']}", "url": item["url"]}
                for item in ENGLISH_AUDIO
            ],
        }
        metadata["goenka_chanting_youtube"] = GOENKA_VIDEO
        metadata["text_note"] = ("Working texts are UTF-8 plain text from SuttaCentral. "
                                 "Roland Kitchen's English reading follows Bhikkhu Sujato's "
                                 "translation; minor edition differences may still exist. "
                                 "Ānandajoti's spoken Pāli follows a different edition.")

        if not args.skip_audio:
            for name, url in AUDIO_ARCHIVES.items():
                print(f"Audio {name}: {url}")
                archive = out / "archives" / f"{name}.zip"
                try:
                    download(session, url, archive)
                    extract_audio(archive, out / "audio" / name)
                except (requests.RequestException, zipfile.BadZipFile, OSError) as error:
                    print(f"WARNING: could not fetch {name}: {error}", file=sys.stderr)

            for item in ENGLISH_AUDIO:
                destination = out / "audio" / "spoken_english" / item["filename"]
                print(f"Audio spoken_english: {item['filename']}")
                try:
                    download(session, item["url"], destination)
                except (requests.RequestException, OSError) as error:
                    print(f"WARNING: could not fetch {item['filename']}: {error}", file=sys.stderr)

        write_atomic(metadata_path, json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        print(f"\nSources directory: {out}")
        print(f"Goenka chanting (link only): {GOENKA_VIDEO}")
        return 0
    except (requests.RequestException, ValueError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
