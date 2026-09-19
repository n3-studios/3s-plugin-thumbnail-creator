// The thumbnail editor, shipped by the plugin and opened by core in an overlay
// iframe. It is served from the app's own origin, so these fetches are
// same-origin: the routes are the plugin's own (registered on the host), and
// the rendered image is a static project file.
//
// Core passes the clip context in the URL and listens for two messages back:
//   { type: 'plugin:changed' } — this clip's data moved; refresh the views.
//   { type: 'plugin:close' }   — close the overlay.

(function () {
  const params = new URLSearchParams(location.search);
  const projectId = params.get('projectId');
  const clipIndex = params.get('clipIndex');
  const base = `/project/${projectId}/clip/${clipIndex}/thumbnail`;

  const el = (id) => document.getElementById(id);
  const preview = el('preview');
  const frame = el('frame');
  const frameLabel = el('frameLabel');
  const showOverlay = el('showOverlay');
  const showCaptions = el('showCaptions');
  const extra = el('extra');
  const status = el('status');

  let settings = null; // the ThumbnailSettings this clip carries

  const setStatus = (text, isError) => {
    status.textContent = text || '';
    status.className = isError ? 'error' : '';
  };

  const notifyChanged = () =>
    window.parent.postMessage({ type: 'plugin:changed' }, '*');

  const showImage = (filename, version) => {
    if (!filename) {
      preview.innerHTML = '<span class="empty">No thumbnail rendered yet.</span>';
      return;
    }
    const v = version ? `?v=${encodeURIComponent(version)}` : '';
    preview.innerHTML =
      `<img alt="Thumbnail" src="/projects/static/${projectId}/thumbnails/${filename}${v}" />`;
  };

  const fillFromSettings = (data) => {
    settings = data.settings || {
      frame_time: 0, show_captions: false, show_overlay: true, extra: null,
      generated_filename: null, generated_at: null,
    };
    frame.max = String(Math.max(0, (data.duration || 0) - 0.05));
    frame.value = String(settings.frame_time || 0);
    frameLabel.textContent = Number(frame.value).toFixed(1);
    showOverlay.checked = settings.show_overlay !== false;
    showCaptions.checked = !!settings.show_captions;
    extra.value = settings.extra && settings.extra.text ? settings.extra.text : '';
    showImage(settings.generated_filename, settings.generated_at);
  };

  const collect = () => {
    const text = extra.value.trim();
    return {
      frame_time: parseFloat(frame.value) || 0,
      show_captions: showCaptions.checked,
      show_overlay: showOverlay.checked,
      extra: text ? { enabled: true, text } : null,
      generated_filename: settings ? settings.generated_filename : null,
      generated_at: settings ? settings.generated_at : null,
    };
  };

  const load = async () => {
    try {
      const res = await fetch(base);
      if (!res.ok) throw new Error(`Load failed (${res.status})`);
      fillFromSettings(await res.json());
      setStatus('');
    } catch (e) {
      setStatus(String(e.message || e), true);
    }
  };

  const save = async () => {
    setStatus('Saving…');
    try {
      const res = await fetch(base, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thumbnail: collect() }),
      });
      if (!res.ok) throw new Error(`Save failed (${res.status})`);
      const data = await res.json();
      if (data.thumbnail) settings = data.thumbnail;
      setStatus('Saved.');
      notifyChanged();
    } catch (e) {
      setStatus(String(e.message || e), true);
    }
  };

  const render = async () => {
    setStatus('Rendering…');
    try {
      // Save first, so the render uses what is on screen.
      await fetch(base, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thumbnail: collect() }),
      });
      const res = await fetch(base, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Render failed (${res.status})`);
      }
      const data = await res.json();
      if (data.thumbnail) settings = data.thumbnail;
      showImage(settings.generated_filename, settings.generated_at);
      setStatus('Rendered.');
      notifyChanged();
    } catch (e) {
      setStatus(String(e.message || e), true);
    }
  };

  frame.addEventListener('input', () => {
    frameLabel.textContent = Number(frame.value).toFixed(1);
  });
  el('save').addEventListener('click', save);
  el('render').addEventListener('click', render);
  el('close').addEventListener('click', () =>
    window.parent.postMessage({ type: 'plugin:close' }, '*')
  );

  load();
})();
