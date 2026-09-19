// The thumbnail editor, shipped by the plugin and opened from the app's Plugins
// menu (OBS-style) scoped to the clips selected on the project page (Sonarr-
// style). It steps through those clips one at a time: scrub to a frame, toggle
// the title and captions, add an extra line, save, and render — the editing the
// per-clip page used to do, now driven from a selection.
//
// Served from the app's own origin, so these are same-origin calls: the per-
// clip routes are the plugin's own, and the source video is a static file.
// Core relays { type: 'plugin:changed' } (refresh the grid) and
// { type: 'plugin:close' } (close the overlay).

(function () {
  const params = new URLSearchParams(location.search);
  const projectId = params.get('projectId');
  const clips = (params.get('clips') || '')
    .split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => Number.isInteger(n));

  const el = (id) => document.getElementById(id);
  const frame = el('frame');
  const titleOverlay = el('titleOverlay');
  const extraOverlay = el('extraOverlay');
  const frameTime = el('frameTime');
  const frameLabel = el('frameLabel');
  const showOverlay = el('showOverlay');
  const showCaptions = el('showCaptions');
  const extra = el('extra');
  const status = el('status');

  let i = 0;               // pointer into clips
  let payload = null;      // last GET for the current clip
  let settings = null;     // mutable copy of payload.settings

  const clip = () => clips[i];
  const base = () => `/project/${projectId}/clip/${clip()}/thumbnail`;

  const setStatus = (text, isError) => {
    status.textContent = text || '';
    status.className = isError ? 'error' : '';
  };

  if (clips.length === 0) {
    setStatus('No clips to edit. Select clips first, or use Render all from the menu.', true);
    ['prev', 'next', 'save', 'renderThis', 'frameTime', 'showOverlay', 'showCaptions', 'extra']
      .forEach((id) => { const n = el(id); if (n) n.disabled = true; });
  }

  const drawOverlay = (node, overlay) => {
    const text = overlay && (overlay.text || '').trim();
    if (!text) { node.hidden = true; return; }
    const h = frame.clientHeight || el('preview').clientHeight || 300;
    node.hidden = false;
    node.textContent = text.replace(/\*/g, '');
    node.style.fontSize = `${Math.max(12, (overlay.font_size_pct || 8) / 100 * h)}px`;
    node.style.top = `${(overlay.position_pct != null ? overlay.position_pct : 12) / 100 * h}px`;
    node.style.fontStyle = overlay.italic ? 'italic' : 'normal';
    node.style.color = overlay.text_color || '#fff';
  };

  const redraw = () => {
    if (!payload) return;
    const at = Math.max(0, payload.start + (settings.frame_time || 0));
    if (Number.isFinite(frame.duration)) frame.currentTime = at;
    frameLabel.textContent = Number(settings.frame_time || 0).toFixed(1);
    drawOverlay(titleOverlay, settings.show_overlay ? payload.title : null);
    drawOverlay(extraOverlay, settings.extra);
  };

  const fillControls = () => {
    frameTime.max = String(Math.max(0, (payload.duration || 0) - 0.05));
    frameTime.value = String(settings.frame_time || 0);
    showOverlay.checked = settings.show_overlay !== false;
    showCaptions.checked = !!settings.show_captions;
    extra.value = settings.extra && settings.extra.text ? settings.extra.text : '';
  };

  const collect = () => {
    const text = extra.value.trim();
    // Keep the extra line's styling if it had some; otherwise a plain default.
    const prevExtra = settings.extra && typeof settings.extra === 'object' ? settings.extra : {};
    return {
      frame_time: parseFloat(frameTime.value) || 0,
      show_captions: showCaptions.checked,
      show_overlay: showOverlay.checked,
      extra: text ? { ...prevExtra, enabled: true, text } : null,
      generated_filename: settings.generated_filename ?? null,
      generated_at: settings.generated_at ?? null,
    };
  };

  const loadClip = async () => {
    el('clipLabel').textContent = `Clip ${clip() + 1} — ${i + 1} of ${clips.length}`;
    setStatus('Loading…');
    try {
      const res = await fetch(base());
      if (!res.ok) throw new Error(`Load failed (${res.status})`);
      payload = await res.json();
      settings = { ...payload.settings };
      if (payload.source_url && frame.getAttribute('src') !== payload.source_url) {
        frame.src = payload.source_url;
        frame.addEventListener('loadedmetadata', redraw, { once: true });
      }
      fillControls();
      redraw();
      setStatus('');
    } catch (e) {
      setStatus(String(e.message || e), true);
    }
  };

  const save = async () => {
    settings = collect();
    setStatus('Saving…');
    try {
      const res = await fetch(base(), {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thumbnail: settings }),
      });
      if (!res.ok) throw new Error(`Save failed (${res.status})`);
      const data = await res.json();
      if (data.thumbnail) settings = data.thumbnail;
      setStatus('Saved.');
      window.parent.postMessage({ type: 'plugin:changed' }, '*');
    } catch (e) {
      setStatus(String(e.message || e), true);
    }
  };

  const renderThis = async () => {
    await save();
    setStatus('Rendering…');
    try {
      const res = await fetch(base(), { method: 'POST' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Render failed (${res.status})`);
      }
      const data = await res.json();
      if (data.thumbnail) settings = data.thumbnail;
      setStatus('Rendered.');
      window.parent.postMessage({ type: 'plugin:changed' }, '*');
    } catch (e) {
      setStatus(String(e.message || e), true);
    }
  };

  const renderAll = async () => {
    setStatus('Rendering all selected…');
    try {
      const res = await fetch('/plugins/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin: 'thumbnail', action: 'render', project_id: projectId, clips }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Failed (${res.status})`);
      }
      window.parent.postMessage({ type: 'plugin:changed' }, '*');
      setStatus('Rendering started. Clips update as it finishes.');
    } catch (e) {
      setStatus(String(e.message || e), true);
    }
  };

  const step = async (delta) => {
    await save();
    i = (i + delta + clips.length) % clips.length;
    await loadClip();
  };

  frameTime.addEventListener('input', () => { settings.frame_time = parseFloat(frameTime.value) || 0; redraw(); });
  showOverlay.addEventListener('change', () => { settings.show_overlay = showOverlay.checked; redraw(); });
  showCaptions.addEventListener('change', () => { settings.show_captions = showCaptions.checked; });
  extra.addEventListener('input', () => {
    const text = extra.value.trim();
    const prev = settings.extra && typeof settings.extra === 'object' ? settings.extra : {};
    settings.extra = text ? { ...prev, enabled: true, text } : null;
    redraw();
  });
  el('prev').addEventListener('click', () => step(-1));
  el('next').addEventListener('click', () => step(1));
  el('save').addEventListener('click', save);
  el('renderThis').addEventListener('click', renderThis);
  el('renderAll').addEventListener('click', renderAll);
  el('close').addEventListener('click', () => window.parent.postMessage({ type: 'plugin:close' }, '*'));

  if (clips.length > 0) loadClip();
})();
