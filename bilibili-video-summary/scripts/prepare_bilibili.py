#!/usr/bin/env python3
"""Prepare a timestamped Bilibili transcript using subtitles or whisper.cpp."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json,text/plain,*/*",
}
API_ROOT = "https://api.bilibili.com"
SUBTITLE_GLOB = "subtitle.*"


class PreparationError(RuntimeError):
    pass


def require_tool(name: str, purpose: str) -> str:
    path = shutil.which(name)
    if not path:
        raise PreparationError(f"Missing {name}, required for {purpose}.")
    return path


def run_live(command: list[str], stage: str) -> None:
    print(f"[{stage}] {shlex.join(command)}", flush=True)
    result = subprocess.run(command)
    if result.returncode != 0:
        raise PreparationError(f"{stage} failed with exit code {result.returncode}.")


def request_json(url: str, stage: str) -> dict:
    request = Request(url, headers=BASE_HEADERS)
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise PreparationError(f"{stage} failed: {exc}") from exc
    if isinstance(payload, dict) and payload.get("code") not in (None, 0):
        raise PreparationError(
            f"{stage} failed: Bilibili code {payload.get('code')} "
            f"({payload.get('message') or payload.get('msg') or 'unknown error'})."
        )
    return payload


def api_json(path: str, params: dict[str, object], stage: str) -> dict:
    return request_json(f"{API_ROOT}{path}?{urlencode(params)}", stage)


def resolve_short_url(source: str) -> str:
    parsed = urlparse(source)
    if parsed.netloc.lower() not in {"b23.tv", "www.b23.tv"}:
        return source
    request = Request(source, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=30) as response:
            return response.geturl()
    except (HTTPError, URLError, TimeoutError) as exc:
        raise PreparationError(f"Could not resolve b23.tv short URL: {exc}") from exc


def normalize_source(source: str, part: int | None) -> str:
    source = source.strip()
    if re.fullmatch(r"BV[0-9A-Za-z]+", source, flags=re.IGNORECASE):
        source = f"https://www.bilibili.com/video/{source}"
    elif re.fullmatch(r"av\d+", source, flags=re.IGNORECASE):
        source = f"https://www.bilibili.com/video/{source}"
    elif not re.match(r"https?://", source):
        raise PreparationError("Expected a bilibili.com/b23.tv URL, BV id, or av id.")

    source = resolve_short_url(source)
    parsed = urlparse(source)
    if not (parsed.netloc.lower().endswith("bilibili.com") or parsed.netloc.lower() == "b23.tv"):
        raise PreparationError("The resolved source is not a Bilibili URL.")
    if part is None:
        return source

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["p"] = str(part)
    return urlunparse(parsed._replace(query=urlencode(query)))


def source_identifier(url: str) -> tuple[str, str]:
    match = re.search(r"/(BV[0-9A-Za-z]+)(?:[/?#]|$)", url, flags=re.IGNORECASE)
    if match:
        return "bvid", match.group(1)
    match = re.search(r"/(?:video/)?av(\d+)(?:[/?#]|$)", url, flags=re.IGNORECASE)
    if match:
        return "aid", match.group(1)
    raise PreparationError("Could not find a BV or av identifier in the resolved URL.")


def requested_part(url: str, explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    query = dict(parse_qsl(urlparse(url).query))
    return int(query["p"]) if query.get("p", "").isdigit() else 1


def load_metadata(url: str, part: int | None) -> dict:
    id_kind, identifier = source_identifier(url)
    payload = api_json("/x/web-interface/view", {id_kind: identifier}, "public metadata")
    data = payload.get("data") or {}
    pages = data.get("pages") or []
    part_number = requested_part(url, part)
    if part_number < 1 or part_number > len(pages):
        raise PreparationError(
            f"Requested part {part_number}, but the video has {len(pages)} part(s)."
        )
    page = pages[part_number - 1]
    bvid = data.get("bvid") or (identifier if id_kind == "bvid" else None)
    aid = data.get("aid") or (int(identifier) if id_kind == "aid" else None)
    canonical = f"https://www.bilibili.com/video/{bvid or f'av{aid}'}"
    if len(pages) > 1 or part_number != 1:
        canonical = f"{canonical}?p={part_number}"

    timestamp = data.get("pubdate")
    upload_date = None
    if isinstance(timestamp, (int, float)):
        upload_date = datetime.fromtimestamp(
            timestamp, ZoneInfo("Asia/Shanghai")
        ).strftime("%Y%m%d")

    video_title = data.get("title")
    part_title = page.get("part")
    title = video_title
    if len(pages) > 1 and part_title:
        title = f"{video_title} · P{part_number} {part_title}"

    return {
        "id": bvid or f"av{aid}",
        "webpage_url": canonical,
        "title": title,
        "video_title": video_title,
        "part_title": part_title,
        "uploader": (data.get("owner") or {}).get("name"),
        "upload_date": upload_date,
        "duration": page.get("duration") or data.get("duration"),
        "description": data.get("desc"),
        "_bili": {
            "bvid": bvid,
            "aid": aid,
            "cid": page.get("cid"),
            "part": part_number,
            "part_count": len(pages),
        },
    }


def safe_component(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("-.")
    return cleaned[:80] or "video"


def cookie_args(browser: str | None) -> list[str]:
    return ["--cookies-from-browser", browser] if browser else []


def write_metadata(path: Path, metadata: dict) -> None:
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def format_srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def subtitle_json_to_srt(payload: dict, target: Path) -> None:
    body = payload.get("body") or []
    blocks: list[str] = []
    for index, item in enumerate(body, start=1):
        text = clean_text(str(item.get("content") or ""))
        if not text:
            continue
        start = format_srt_time(float(item.get("from") or 0))
        end = format_srt_time(float(item.get("to") or item.get("from") or 0))
        blocks.append(f"{index}\n{start} --> {end}\n{text}")
    if not blocks:
        raise PreparationError("The selected platform subtitle track was empty.")
    target.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def subtitle_to_timestamped_text(source: Path, target: Path) -> None:
    content = source.read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\r?\n\s*\r?\n", content.strip())
    timestamp = re.compile(r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.]\d+\s+-->")
    output: list[str] = []
    previous = ""

    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        time_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if time_index is None:
            continue
        match = timestamp.search(lines[time_index])
        if not match:
            continue
        text = clean_text(" ".join(lines[time_index + 1 :]))
        if not text or text == previous:
            continue
        stamp = f"{int(match.group('h')):02d}:{match.group('m')}:{match.group('s')}"
        output.append(f"[{stamp}] {text}")
        previous = text

    if not output:
        raise PreparationError(f"No timed text could be parsed from {source.name}.")
    target.write_text("\n".join(output) + "\n", encoding="utf-8")


def subtitle_track_rank(track: dict) -> tuple[int, int, str]:
    language = str(track.get("lan") or "").lower()
    language_priority = ["zh-hans", "zh-cn", "zh-tw", "zh-hant", "zh", "ai-zh"]
    try:
        lang_rank = language_priority.index(language)
    except ValueError:
        lang_rank = len(language_priority)
    ai_rank = 1 if track.get("ai_type") or str(track.get("type")) == "1" else 0
    return ai_rank, lang_rank, language


def try_public_subtitles(context: dict, output_dir: Path) -> Path | None:
    bili = context["_bili"]
    payload = api_json(
        "/x/player/v2",
        {"bvid": bili.get("bvid"), "aid": bili.get("aid"), "cid": bili["cid"]},
        "subtitle list",
    )
    tracks = ((payload.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    if not tracks:
        return None
    track = sorted(tracks, key=subtitle_track_rank)[0]
    subtitle_url = track.get("subtitle_url") or track.get("url")
    if not subtitle_url:
        return None
    if subtitle_url.startswith("//"):
        subtitle_url = "https:" + subtitle_url
    subtitle_payload = request_json(subtitle_url, "subtitle download")
    language = safe_component(str(track.get("lan") or "unknown"))
    target = output_dir / f"subtitle.{language}.srt"
    subtitle_json_to_srt(subtitle_payload, target)
    return target


def try_ytdlp_subtitles(
    url: str,
    output_dir: Path,
    browser: str | None,
    sub_langs: str,
) -> Path | None:
    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        return None
    template = str(output_dir / "subtitle.%(ext)s")
    command = [
        yt_dlp,
        "--no-warnings",
        "--no-playlist",
        "--skip-download",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs",
        sub_langs,
        "--sub-format",
        "srt/vtt/best",
        "--convert-subs",
        "srt",
        "--user-agent",
        USER_AGENT,
        "--referer",
        "https://www.bilibili.com/",
        "-o",
        template,
        *cookie_args(browser),
        url,
    ]
    print(f"[yt-dlp subtitle fallback] {shlex.join(command)}", flush=True)
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        print(f"[yt-dlp subtitle fallback] unavailable: {detail[-2000:]}", file=sys.stderr)
        return None
    candidates = sorted(output_dir.glob("subtitle*.srt"))
    return candidates[0] if candidates else None


def try_subtitles(
    context: dict,
    output_dir: Path,
    browser: str | None,
    sub_langs: str,
) -> Path | None:
    try:
        subtitle = try_public_subtitles(context, output_dir)
        if subtitle:
            return subtitle
    except PreparationError as exc:
        print(f"[public subtitles] unavailable: {exc}", file=sys.stderr)
    return try_ytdlp_subtitles(context["webpage_url"], output_dir, browser, sub_langs)


def locate_model(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.environ.get("WHISPER_CPP_MODEL"):
        candidates.append(Path(os.environ["WHISPER_CPP_MODEL"]).expanduser())

    skill_root = Path(__file__).resolve().parents[1]
    candidates.append(skill_root / "models" / "ggml-small.bin")
    codex_root = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    candidates.append(codex_root / "skills" / "douyin-video-summary" / "models" / "ggml-small.bin")

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise PreparationError("No whisper.cpp model found. Pass --model or set WHISPER_CPP_MODEL.")


def download_stream(url: str, target: Path, referer: str) -> None:
    headers = dict(BASE_HEADERS)
    headers["Referer"] = referer
    request = Request(url, headers=headers)
    host = urlparse(url).netloc
    print(f"[audio download] fetching media from {host}", flush=True)
    try:
        with urlopen(request, timeout=60) as response, target.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
    except (HTTPError, URLError, TimeoutError) as exc:
        target.unlink(missing_ok=True)
        raise PreparationError(f"media download from {host} failed: {exc}") from exc
    if target.stat().st_size < 1024:
        target.unlink(missing_ok=True)
        raise PreparationError(f"media download from {host} returned an invalid small file")


def public_audio_urls(context: dict) -> list[tuple[str, str]]:
    bili = context["_bili"]
    payload = api_json(
        "/x/player/playurl",
        {
            "bvid": bili.get("bvid"),
            "aid": bili.get("aid"),
            "cid": bili["cid"],
            "qn": 64,
            "fnval": 16,
            "fourk": 1,
        },
        "audio stream list",
    )
    data = payload.get("data") or {}
    audio_entries = sorted(
        ((data.get("dash") or {}).get("audio") or []),
        key=lambda item: item.get("bandwidth") or 0,
        reverse=True,
    )
    candidates: list[tuple[str, str]] = []
    for entry in audio_entries:
        urls = [
            entry.get("baseUrl") or entry.get("base_url"),
            *((entry.get("backupUrl") or entry.get("backup_url") or [])),
        ]
        candidates.extend((url, ".m4s") for url in urls if url)
    if not candidates:
        for entry in data.get("durl") or []:
            url = entry.get("url")
            if url:
                candidates.append((url, ".mp4"))
            candidates.extend((backup, ".mp4") for backup in entry.get("backup_url") or [])
    return candidates


def download_audio_ytdlp(url: str, output_dir: Path, browser: str | None) -> Path:
    yt_dlp = require_tool("yt-dlp", "authenticated Bilibili audio fallback")
    command = [
        yt_dlp,
        "--no-warnings",
        "--no-playlist",
        "--user-agent",
        USER_AGENT,
        "--referer",
        "https://www.bilibili.com/",
        "-f",
        "bestaudio/best",
        "-x",
        "--audio-format",
        "m4a",
        "--audio-quality",
        "0",
        "-o",
        str(output_dir / "audio.%(ext)s"),
        *cookie_args(browser),
        url,
    ]
    run_live(command, "yt-dlp audio fallback")
    candidates = [
        path
        for path in output_dir.glob("audio.*")
        if path.is_file() and not path.name.endswith((".part", ".ytdl"))
    ]
    if not candidates:
        raise PreparationError("yt-dlp completed but no audio file was created.")
    return max(candidates, key=lambda path: path.stat().st_size)


def download_audio(context: dict, output_dir: Path, browser: str | None) -> Path:
    errors: list[str] = []
    try:
        candidates = public_audio_urls(context)
    except PreparationError as exc:
        candidates = []
        errors.append(str(exc))
    for index, (url, suffix) in enumerate(candidates, start=1):
        target = output_dir / f"audio{suffix}"
        try:
            download_stream(url, target, context["webpage_url"])
            return target
        except PreparationError as exc:
            errors.append(f"candidate {index}: {exc}")

    if shutil.which("yt-dlp"):
        try:
            return download_audio_ytdlp(context["webpage_url"], output_dir, browser)
        except PreparationError as exc:
            errors.append(str(exc))
    raise PreparationError("No audio route succeeded:\n" + "\n".join(errors[-5:]))


def transcribe_audio(audio: Path, output_dir: Path, model: Path, language: str) -> None:
    ffmpeg = require_tool("ffmpeg", "audio conversion")
    whisper = require_tool("whisper-cli", "local speech recognition")
    wav = output_dir / "transcript.wav"
    prefix = output_dir / "transcript"

    run_live(
        [
            ffmpeg,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(audio),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(wav),
        ],
        "audio conversion",
    )
    try:
        run_live(
            [
                whisper,
                "-m",
                str(model),
                "-l",
                language,
                "-f",
                str(wav),
                "-otxt",
                "-osrt",
                "-of",
                str(prefix),
                "-pp",
            ],
            "whisper.cpp transcription",
        )
    finally:
        wav.unlink(missing_ok=True)

    srt = output_dir / "transcript.srt"
    if not srt.is_file():
        raise PreparationError("whisper.cpp completed but transcript.srt is missing.")
    subtitle_to_timestamped_text(srt, output_dir / "transcript.txt")


def cleanup_generated(output_dir: Path) -> None:
    for name in ("transcript.txt", "transcript.srt", "transcript.wav", "metadata.json"):
        (output_dir / name).unlink(missing_ok=True)
    for pattern in (SUBTITLE_GLOB, "audio.*"):
        for path in output_dir.glob(pattern):
            if path.is_file():
                path.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract Bilibili subtitles or fall back to local whisper.cpp transcription."
    )
    parser.add_argument("source", help="Bilibili URL, b23.tv URL, BV id, or av id")
    parser.add_argument(
        "--output-root",
        default=str(Path.cwd() / "output" / "bilibili"),
        help="Root output directory (default: ./output/bilibili)",
    )
    parser.add_argument("--part", type=int, help="Select part N of a multi-part video")
    parser.add_argument("--language", default="zh", help="Whisper language or auto")
    parser.add_argument("--model", help="Path to a whisper.cpp GGML model")
    parser.add_argument(
        "--sub-langs",
        default="zh-Hans,zh-CN,zh-TW,zh,ai-zh,zh.*",
        help="yt-dlp fallback subtitle language selection",
    )
    parser.add_argument("--force", action="store_true", help="Rebuild existing output")
    parser.add_argument("--force-asr", action="store_true", help="Ignore subtitles and run Whisper")
    parser.add_argument(
        "--subtitle-only",
        action="store_true",
        help="Fail instead of downloading audio when subtitles are unavailable",
    )
    parser.add_argument("--keep-audio", action="store_true", help="Keep compressed audio after ASR")
    parser.add_argument(
        "--cookies-from-browser",
        metavar="NAME",
        help="Use an explicitly authorized browser cookie store for yt-dlp fallback",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.part is not None and args.part < 1:
        raise PreparationError("--part must be at least 1.")
    if args.force_asr and args.subtitle_only:
        raise PreparationError("--force-asr and --subtitle-only cannot be combined.")

    url = normalize_source(args.source, args.part)
    raw = load_metadata(url, args.part)
    part = raw["_bili"]["part"]
    identity = str(raw.get("id") or "video")
    key = safe_component(identity)
    if raw["_bili"].get("part_count", 1) > 1 or part != 1:
        key = f"{key}-p{part}"

    output_dir = Path(args.output_root).expanduser().resolve() / key
    output_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = output_dir / "transcript.txt"
    metadata_path = output_dir / "metadata.json"

    if transcript_path.is_file() and metadata_path.is_file() and not (args.force or args.force_asr):
        print(f"[reuse] {transcript_path}")
        print(f"[reuse] {metadata_path}")
        return 0
    if args.force or args.force_asr:
        cleanup_generated(output_dir)

    result_metadata = {
        "source_url": args.source,
        "canonical_url": raw.get("webpage_url") or url,
        "video_id": identity,
        "part": part,
        "part_count": raw["_bili"].get("part_count"),
        "cid": raw["_bili"].get("cid"),
        "title": raw.get("title"),
        "video_title": raw.get("video_title"),
        "part_title": raw.get("part_title"),
        "uploader": raw.get("uploader"),
        "upload_date": raw.get("upload_date"),
        "duration_seconds": raw.get("duration"),
        "description": raw.get("description"),
        "transcript_source": None,
        "language": args.language,
        "status": "processing",
    }
    write_metadata(metadata_path, result_metadata)

    subtitle = None
    if not args.force_asr:
        subtitle = try_subtitles(raw, output_dir, args.cookies_from_browser, args.sub_langs)
    if subtitle:
        target_srt = output_dir / "transcript.srt"
        shutil.copy2(subtitle, target_srt)
        subtitle_to_timestamped_text(target_srt, transcript_path)
        selected_track = subtitle.name
        for candidate in output_dir.glob(SUBTITLE_GLOB):
            candidate.unlink(missing_ok=True)
        result_metadata.update(
            {
                "transcript_source": "platform_subtitle",
                "subtitle_track": selected_track,
                "status": "complete",
            }
        )
    else:
        if args.subtitle_only:
            result_metadata["status"] = "no_subtitle"
            write_metadata(metadata_path, result_metadata)
            raise PreparationError("No matching subtitle track was available.")
        model = locate_model(args.model)
        audio = download_audio(raw, output_dir, args.cookies_from_browser)
        transcribe_audio(audio, output_dir, model, args.language)
        if not args.keep_audio:
            audio.unlink(missing_ok=True)
        result_metadata.update(
            {
                "transcript_source": "whisper.cpp",
                "whisper_model": str(model),
                "status": "complete",
            }
        )

    write_metadata(metadata_path, result_metadata)
    print(f"[complete] {transcript_path}")
    print(f"[complete] {output_dir / 'transcript.srt'}")
    print(f"[complete] {metadata_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreparationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
