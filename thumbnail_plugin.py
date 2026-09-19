"""Thumbnail plugin — a still per clip, drawn like the clip.

This is the whole plugin. It imports nothing from the host application: it is
handed a `host` at `register`, and everything it does — reading a clip's timing
and settings, writing its own thumbnail data, finding where to put the image —
goes through that host. Rendering is vendored in `render.py`.

The one metadata key it owns is `highlights[].thumbnail`; it reads whatever else
it needs (the source file, project settings, the clip's title fields) and writes
only that.
"""

import logging
from datetime import datetime

import render

logger = logging.getLogger(__name__)

STEP = "thumbnails"
DIRECTORY = "thumbnails"
OWNED_KEY = "thumbnail"


def _default_settings() -> dict:
    return {
        "frame_time": 0.0,
        "show_captions": False,
        "show_overlay": True,
        "extra": None,
        "generated_filename": None,
        "generated_at": None,
    }


def _settings_of(highlight: dict) -> dict:
    stored = highlight.get(OWNED_KEY)
    settings = _default_settings()
    if isinstance(stored, dict):
        settings.update({k: stored.get(k, settings[k]) for k in settings})
    return settings


def _clip_duration(highlight: dict) -> float:
    return max(0.0, float(highlight.get("end", 0.0)) - float(highlight.get("start", 0.0)))


def _frame_time(highlight: dict, settings: dict) -> float:
    duration = _clip_duration(highlight)
    if duration <= 0:
        return 0.0
    return max(0.0, min(float(settings.get("frame_time", 0.0)), max(0.0, duration - 0.05)))


# What a title starts from when a clip has no overlay of its own. Mirrors the
# app's DEFAULT_OVERLAY_TEXT (frontend api.ts / backend OverlayText) so the
# preview draws the model's line in the same look the burn uses.
_DEFAULT_OVERLAY = {
    "enabled": True,
    "text": "",
    "start": 0,
    "duration": 3,
    "fade_in": 0,
    "fade_out": 0.6,
    "font_family": "Arial Black",
    "font_size_pct": 8,
    "bold": True,
    "italic": False,
    "uppercase": True,
    "text_color": "#FFFFFF",
    "outline_color": "#000000",
    "outline_pct": 0.9,
    "shadow_color": "#000000",
    "shadow_pct": 0.8,
    "highlight_color": "#FFE000",
    "box_color": None,
    "position_pct": 12,
    "max_width_pct": 86,
}


def _title_overlay(highlight: dict, settings: dict):
    """The title as an OverlayText, or None — the shape the UI preview draws.

    The clip's own overlay when it carries text, otherwise the model's line
    (the thumbnail line, then the hook, then the YouTube title) drawn in the
    clip's overlay style, or the default style when the clip has none.
    """
    if not settings.get("show_overlay", True):
        return None
    overlay = highlight.get("overlay") if isinstance(highlight.get("overlay"), dict) else None
    if overlay and overlay.get("enabled") and (overlay.get("text") or "").strip():
        return {**overlay, "text": overlay["text"].strip()[:200]}

    text = (
        (highlight.get("thumbnail_text") or "")
        or (highlight.get("viral_hook_text") or "")
        or (highlight.get("video_title_for_youtube_short") or "")
    ).strip()
    if not text:
        return None
    base = overlay or _DEFAULT_OVERLAY
    return {**_DEFAULT_OVERLAY, **base, "enabled": True, "text": text[:200]}


def _burn_text(overlay, settings: dict) -> str:
    """The plain text ffmpeg burns: the title, then any extra line.

    The asterisks the model marks a word with are for the app's coloured
    renderer; here they would just print, so they are dropped.
    """
    parts = []
    if overlay and (overlay.get("text") or "").strip():
        parts.append(overlay["text"].strip())
    extra = settings.get("extra")
    if isinstance(extra, dict) and (extra.get("text") or "").strip():
        parts.append(extra["text"].strip())
    return " ".join(parts).replace("*", "")[:200]


class ThumbnailStep:
    """The pipeline step: draw every clip's still. Runs on the plugin `host`."""

    def __init__(self, host):
        self._host = host

    def _source_path(self, meta):
        files = meta.read("files", {}) or {}
        return meta.project_dir() / files.get("original_file", "")

    def _render_one(self, meta, index: int) -> None:
        highlight = meta.read_highlight(index)
        settings = _settings_of(highlight)
        source = self._source_path(meta)
        if not source.exists():
            raise FileNotFoundError(
                "The source video for this project is missing, so no frame can be taken from it."
            )
        project_settings = meta.read("settings", {}) or {}
        at = _frame_time(highlight, settings)
        filename = f"clip_{index:03d}.jpg"
        output = meta.project_dir() / DIRECTORY / filename
        overlay = _title_overlay(highlight, settings)

        render.render_still(
            input_path=str(source),
            output_path=str(output),
            at_source_seconds=float(highlight.get("start", 0.0)) + at,
            aspect_ratio=project_settings.get("aspect_ratio", "keep original"),
            resolution=project_settings.get("resolution", "keep original"),
            layout=project_settings.get("clip_layout", "fill"),
            band_dim_pct=project_settings.get("band_dim_pct"),
            title=_burn_text(overlay, settings),
        )

        settings["generated_filename"] = filename
        settings["generated_at"] = datetime.now().isoformat()
        meta.write_highlight(index, OWNED_KEY, settings)

    def execute(self, project, full: bool = False):
        """Redraw the still for every clip. Raises on a whole-step failure.

        Per-clip failures are logged and skipped so one bad frame does not cost
        the rest; core marks the step completed when this returns without
        raising, and records a raised failure as the step's error.
        """
        meta = self._host.metadata(project.project_id)
        count = meta.highlight_count()
        logger.info(f"Thumbnail step drawing {count} still(s) for {project.project_id}")
        failures = 0
        for index in range(count):
            try:
                self._render_one(meta, index)
            except FileNotFoundError:
                raise
            except Exception as e:
                failures += 1
                logger.warning(f"Skipped thumbnail for clip {index}: {e}")
        if count and failures == count:
            raise RuntimeError("No thumbnail could be drawn for any clip.")


def _preview_payload(meta, index: int) -> dict:
    """What the editor dialog needs to draw and scrub one clip's thumbnail.

    The source video (not the cut clip) and the clip's start, so the dialog can
    scrub to `start + frame_time`; the resolved title as an OverlayText for the
    live overlay; and what the last render produced.
    """
    highlight = meta.read_highlight(index)
    settings = _settings_of(highlight)
    files = meta.read("files", {}) or {}
    original = files.get("original_file")
    source_url = f"/projects/static/{meta.project_dir().name}/{original}" if original else None
    filename = settings.get("generated_filename")
    exists = bool(filename and (meta.project_dir() / DIRECTORY / filename).exists())
    return {
        "settings": settings,
        "title": _title_overlay(highlight, settings),
        "title_font": None,
        "start": float(highlight.get("start", 0.0)),
        "duration": _clip_duration(highlight),
        "source_url": source_url,
        "exists": exists,
    }


def register(host):
    """Wire the plugin in: a step, a run action, per-clip editor routes, a hook.

    The Plugins menu opens the editor dialog (which uses the routes) and runs
    the action on the selected clips or on all of them.
    """
    step = ThumbnailStep(host)
    host.register_step(STEP, step, {"command": STEP, "depends_on": ["highlights"], "auto_run": False})

    def render(project_id, clips):
        """Render stills for the given clip indices, or every clip when None.

        Runs in the background; per-clip failures are logged and skipped so one
        bad frame does not cost the rest. Each render writes its result onto the
        highlight through the metadata manager, which is how the grid learns a
        still now exists.
        """
        meta = host.metadata(project_id)
        indices = (
            [int(i) for i in clips]
            if clips is not None
            else list(range(meta.highlight_count()))
        )
        for index in indices:
            try:
                step._render_one(meta, index)
            except Exception as e:
                logger.warning(f"Skipped thumbnail for clip {index}: {e}")

    host.register_action("render", "Render thumbnails", render)

    # Per-clip editor routes the dialog calls: read one clip's settings + frame,
    # save them, and render one clip now.
    def get_thumbnail(request, project_id, index):
        meta = host.metadata(project_id)
        try:
            payload = _preview_payload(meta, int(index))
        except IndexError:
            request.send_cors_error(404, "No clip at that index")
            return
        request.send_json_response(payload)

    def put_thumbnail(request, project_id, index):
        import json
        length = int(request.headers.get("Content-Length", 0))
        body = json.loads(request.rfile.read(length)) if length else {}
        meta = host.metadata(project_id)
        index = int(index)
        try:
            highlight = meta.read_highlight(index)
        except IndexError:
            request.send_cors_error(404, f"No clip at index {index}")
            return
        payload = body.get("thumbnail")
        if isinstance(payload, dict):
            settings = _default_settings()
            settings.update({k: payload.get(k, settings[k]) for k in settings})
            # A settings save describes the next render; keep what a render
            # already produced so it is not orphaned.
            stored = highlight.get(OWNED_KEY)
            if isinstance(stored, dict):
                settings["generated_filename"] = stored.get("generated_filename")
                settings["generated_at"] = stored.get("generated_at")
            meta.write_highlight(index, OWNED_KEY, settings)
            request.send_json_response({"status": "success", "thumbnail": settings})
        else:
            meta.write_highlight(index, OWNED_KEY, None)
            request.send_json_response({"status": "success", "thumbnail": None})

    def post_thumbnail(request, project_id, index):
        meta = host.metadata(project_id)
        try:
            step._render_one(meta, int(index))
        except IndexError:
            request.send_cors_error(404, "No clip at that index")
            return
        except FileNotFoundError as e:
            request.send_cors_error(409, str(e))
            return
        request.send_json_response({
            "status": "success",
            "thumbnail": meta.read_highlight(int(index)).get(OWNED_KEY),
        })

    host.register_route("GET", "/project/{project_id}/clip/{index}/thumbnail", get_thumbnail)
    host.register_route("PUT", "/project/{project_id}/clip/{index}/thumbnail", put_thumbnail)
    host.register_route("POST", "/project/{project_id}/clip/{index}/thumbnail", post_thumbnail)

    def on_highlight_deleted(project_id, index, removed):
        """Delete the still made for a highlight that has been removed."""
        stored = removed.get(OWNED_KEY) if isinstance(removed, dict) else None
        filename = stored.get("generated_filename") if isinstance(stored, dict) else None
        if not filename:
            return
        path = host.metadata(project_id).project_dir() / DIRECTORY / filename
        path.unlink(missing_ok=True)

    host.on_highlight_deleted(on_highlight_deleted)
    logger.info("Thumbnail plugin registered")
