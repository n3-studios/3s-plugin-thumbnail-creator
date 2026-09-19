"""Self-contained still rendering for the thumbnail plugin.

Nothing here imports the host application. A frame is pulled from the source
video with ffmpeg, cropped and scaled the way a clip is, and the clip's title
is drawn over it with a frozen ASS subtitle burned by ffmpeg's libass filter —
the same tools the app's own renderer uses, carried here so the plugin does not
reach into it.

Two deliberate departures from the in-app renderer this was extracted from,
both documented so nobody mistakes them for bugs:

* No subject detection. The app centres its crop on a person found with a YOLO
  model (cv2 + ultralytics); carrying that model into the plugin would drag its
  whole dependency stack along. The still is centre-cropped instead. For most
  shorts, shot roughly centred, the difference is small.
* Overlay only, no burned captions. `show_captions` needs the word map and the
  caption styling, which live in the app. The plugin draws the title overlay,
  which is what a thumbnail is for.
"""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

# Vendored copies of the app's aspect-ratio and resolution maps. Data, not code
# — kept here so the plugin resolves output dimensions without reading the
# app's config directory.
ASPECT_RATIOS = {"16:9": "16:9", "9:16": "9:16", "1:1": "1:1", "4:3": "4:3"}
RESOLUTIONS = {
    "480p": "854x480", "720p": "1280x720", "1080p": "1920x1080",
    "1440p": "2560x1440", "2k": "2048x1080", "4k": "3840x2160",
}

STACKED_LAYOUT = "stacked"
DEFAULT_BAND_DIM_PCT = 70.0


def _make_even(value: int) -> int:
    return int(value) - (int(value) % 2)


def probe_video(input_path: str) -> Tuple[int, int]:
    """(width, height) of a video's first video stream, via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", input_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {input_path}: {result.stderr.strip()}")
    stream = json.loads(result.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def resolve_target_dimensions(src_w: int, src_h: int, aspect_ratio: str,
                              resolution: str) -> Tuple[int, int]:
    """Output size for an aspect ratio and resolution — the app's own logic."""
    ratio = ASPECT_RATIOS.get(aspect_ratio, aspect_ratio)
    if ":" in ratio:
        ar_w, ar_h = map(int, ratio.split(":"))
    else:
        ar_w, ar_h = src_w, src_h

    if resolution == "keep original":
        if ar_w / ar_h >= src_w / src_h:
            target_w, target_h = src_w, int(src_w * ar_h / ar_w)
        else:
            target_w, target_h = int(src_h * ar_w / ar_h), src_h
    else:
        target_w, target_h = map(int, RESOLUTIONS.get(resolution, resolution).split("x"))
    return _make_even(target_w), _make_even(target_h)


def calculate_crop_rect(src_w: int, src_h: int, target_w: int, target_h: int,
                        subject_x: float = 0.5, subject_y: float = 0.5) -> Tuple[int, int, int, int]:
    """Crop (w, h, x, y) in source coordinates that frames the centre."""
    scale = max(target_w / src_w, target_h / src_h)
    crop_w = _make_even(min(src_w, round(target_w / scale)))
    crop_h = _make_even(min(src_h, round(target_h / scale)))
    crop_x = _make_even(int(max(0, min(subject_x * src_w - crop_w / 2, src_w - crop_w))))
    crop_y = _make_even(int(max(0, min(subject_y * src_h - crop_h / 2, src_h - crop_h))))
    return crop_w, crop_h, crop_x, crop_y


def _band_brightness(dim_pct: Optional[float]) -> float:
    try:
        dim = float(dim_pct)
    except (TypeError, ValueError):
        dim = DEFAULT_BAND_DIM_PCT
    if not 0.0 <= dim <= 100.0:
        dim = DEFAULT_BAND_DIM_PCT
    return round(1.0 - dim / 100.0, 4)


def build_stacked_filter(src_w: int, src_h: int, target_w: int, target_h: int,
                         dim_pct: Optional[float] = DEFAULT_BAND_DIM_PCT) -> Optional[str]:
    """The three-band stacked filtergraph, or None when it does not apply."""
    if src_w <= 0 or src_h <= 0:
        return None
    centre = _make_even(round(target_w * src_h / src_w))
    if centre >= target_h:
        return None
    band = _make_even((target_h - centre) // 2)
    if band <= 0:
        return None
    top_h, centre_h, bottom_h = band, target_h - 2 * band, band

    def cover(height: int) -> str:
        return (f"scale={target_w}:{height}:force_original_aspect_ratio=increase,"
                f"crop={target_w}:{height}")

    dim = _band_brightness(dim_pct)
    dimmed = f"colorchannelmixer=rr={dim}:gg={dim}:bb={dim}"
    return (
        "split=3[stacktop][stackmid][stackbot];"
        f"[stacktop]{cover(top_h)},{dimmed}[stacktopout];"
        f"[stackmid]{cover(centre_h)}[stackmidout];"
        f"[stackbot]{cover(bottom_h)},{dimmed}[stackbotout];"
        "[stacktopout][stackmidout][stackbotout]vstack=inputs=3,setsar=1"
    )


def _escape_filter_path(path: str) -> str:
    return path.replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'")


def _picture_filters(src_w: int, src_h: int, target_w: int, target_h: int,
                     layout: str, band_dim_pct: Optional[float]) -> list:
    if layout == STACKED_LAYOUT:
        stacked = build_stacked_filter(src_w, src_h, target_w, target_h, band_dim_pct)
        if stacked:
            return [stacked]
    crop_w, crop_h, crop_x, crop_y = calculate_crop_rect(src_w, src_h, target_w, target_h)
    filters = []
    if (crop_w, crop_h) != (src_w, src_h):
        filters.append(f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}")
    if (crop_w, crop_h) != (target_w, target_h):
        filters.append(f"scale={target_w}:{target_h}")
    return filters


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _render_title_ass(text: str, width: int, height: int) -> str:
    """A frozen one-line ASS script drawing `text` centred over the still.

    White with a heavy black outline so it reads over any frame, sized to the
    output height. libass falls back to a default face when the named font is
    absent, so no font file has to travel with the plugin.
    """
    safe = text.replace("\n", " ").replace("{", "(").replace("}", ")").strip()
    font_size = max(24, int(height * 0.08))
    margin_v = int(height * 0.10)
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
        "BackColour, Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
        f"Style: Thumb,Arial,{font_size},&H00FFFFFF,&H00000000,&H00000000,"
        f"-1,4,1,8,40,40,{margin_v}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,{_ass_time(0)},{_ass_time(10)},Thumb,,0,0,0,,{safe}\n"
    )


def render_still(input_path: str, output_path: str, at_source_seconds: float,
                 aspect_ratio: str, resolution: str, layout: str = "fill",
                 band_dim_pct: Optional[float] = None,
                 title: Optional[str] = None) -> None:
    """Write one JPEG still, framed like the clip, with the title drawn on it.

    `at_source_seconds` is an absolute position in the source video (the clip's
    start plus the chosen offset).
    """
    src_w, src_h = probe_video(input_path)
    target_w, target_h = resolve_target_dimensions(src_w, src_h, aspect_ratio, resolution)
    filters = _picture_filters(src_w, src_h, target_w, target_h, layout, band_dim_pct)

    subtitle_path = None
    if title and title.strip():
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix=".ass", prefix="thumb_", encoding="utf-8", delete=False
        )
        with handle as scratch:
            scratch.write(_render_title_ass(title, target_w, target_h))
        subtitle_path = str(Path(handle.name).resolve())
        filters.append(f"subtitles='{_escape_filter_path(subtitle_path)}'")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-nostdin", "-loglevel", "error",
        "-ss", str(max(0.0, at_source_seconds)),
        "-i", input_path,
        "-map", "0:v:0",
        "-frames:v", "1",
    ]
    if filters:
        cmd += ["-vf", ",".join(filters)]
    cmd += ["-q:v", "2", output_path]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {result.stderr.strip()}")
    finally:
        if subtitle_path:
            Path(subtitle_path).unlink(missing_ok=True)
