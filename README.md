# open-clip thumbnail plugin

Draws one still per clip and stores it on the clip, as an open-clip plugin.

This directory is what would live in its own Git repository and be installed
with:

```
python backend/cli.py plugin install <this repo's URL>
# or: POST /plugins/install  {"url": "..."}
```

It is loaded on the next app start. The app never imports this code directly;
the plugin registers a `thumbnails` pipeline step, the three
`/project/{id}/clip/{index}/thumbnail` routes (GET/PUT/POST) the UI calls, and a
cleanup hook that deletes a clip's still when the clip is deleted.

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

## Known differences from the old in-app thumbnailer

- **Centre crop, no subject detection.** The in-app renderer centred its crop on
  a person detected with a YOLO model (`cv2` + `ultralytics`). Carrying that
  model here would pull in its whole dependency stack, so the still is centre
  cropped. For a shot framed roughly centre the difference is small.
- **Overlay only.** The `show_captions` option (burning the transcript onto the
  still) needs the word map and caption styling, which live in the app. The
  plugin draws the title overlay, which is the thumbnail's job.
