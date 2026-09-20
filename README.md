# open-clip thumbnail plugin

Draws one still per clip and stores it on the clip, as an open-clip plugin.

This directory is what would live in its own Git repository and be installed
from the app's Plugins page (or `POST /plugins/install {"url": "..."}`). It is
loaded in place at once. The app never imports this code directly; the plugin
registers a `thumbnails` pipeline step, a `render` action (run on the selected
clips, or all, from the Plugins menu), the per-clip editor routes
`/project/{id}/clip/{index}/thumbnail` (GET/PUT/POST) the dialog uses, a
`thumbnail` UI screen (the editor dialog), and a cleanup hook that deletes a
clip's still when the clip is deleted.

## What it touches

- **Reads** (through the host's metadata manager): the source video path, the
  project's render settings, and each clip's timing and title fields.
- **Writes** only the metadata key it declares it owns, `highlights[].thumbnail`
  — the frame chosen, the toggles, and what the last render produced.
- **Secrets**: none. If it needed any, they would go through the host's secrets
  manager and be stored as `thumbnail.<name>`.

It imports nothing from the application. Rendering (frame extraction, crop/scale
to match the clip, title overlay) is vendored in `render.py` and shells out to
`ffmpeg`/`ffprobe`.

## Framing and fidelity

- **Subject-centred crop.** The still is framed on the same subject the clip is,
  using the app's own detection: the plugin asks the host for the subject centre
  (data) and crops to it, rather than shipping the YOLO model. It falls back to a
  centre crop when nothing is found or the source is missing.
- **Title in the project's overlay style.** The title (and any extra line) is
  drawn with the clip's overlay style — font, size, position, colour, outline,
  the marked `*word*` — so the still's title matches the video's. The one thing
  the plugin cannot read is the font's height ratio (a font-metrics lookup that
  lives in the app), so the title size can differ by a few percent; libass
  resolves the same face.

## Not supported

- **Captions on the still.** A thumbnail here is a frame with its *title*, not
  the rolling subtitles. The `show_captions` setting is carried but the render
  does not burn transcript captions onto the still.
