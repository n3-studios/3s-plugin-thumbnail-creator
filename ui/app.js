// The thumbnail plugin's dialog, opened from the app's Plugins menu (OBS-style)
// and scoped to the clips the user selected on the project page. It is served
// from the app's own origin, so /plugins/run is a same-origin call.
//
// Context arrives in the URL: projectId, and clips as a comma-separated list of
// highlight indices (empty means the user selected none — "render all" is then
// the sensible action).
//
// Core listens for two messages back:
//   { type: 'plugin:changed' } — thumbnails changed; refresh the grid.
//   { type: 'plugin:close' }   — close the overlay.

(function () {
  const params = new URLSearchParams(location.search);
  const projectId = params.get('projectId');
  const clips = (params.get('clips') || '')
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s !== '')
    .map((s) => parseInt(s, 10))
    .filter((n) => Number.isInteger(n));

  const el = (id) => document.getElementById(id);
  const status = el('status');
  const selectedBtn = el('renderSelected');

  el('summary').textContent =
    clips.length > 0
      ? `${clips.length} selected clip${clips.length === 1 ? '' : 's'}`
      : 'the selected clips';

  // Nothing selected — only "Render all" makes sense.
  if (clips.length === 0) selectedBtn.disabled = true;

  const setStatus = (text, isError) => {
    status.textContent = text || '';
    status.className = isError ? 'error' : '';
  };

  const run = async (which) => {
    setStatus('Starting…');
    try {
      const body = {
        plugin: 'thumbnail',
        action: 'render',
        project_id: projectId,
      };
      if (which === 'selected') body.clips = clips;
      const res = await fetch('/plugins/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `Failed (${res.status})`);
      }
      // The render runs in the background; the grid refreshes when the job
      // leaves /active_processes. Tell core to start watching.
      window.parent.postMessage({ type: 'plugin:changed' }, '*');
      setStatus('Rendering started. The clips update as it finishes.');
    } catch (e) {
      setStatus(String(e.message || e), true);
    }
  };

  selectedBtn.addEventListener('click', () => run('selected'));
  el('renderAll').addEventListener('click', () => run('all'));
  el('close').addEventListener('click', () =>
    window.parent.postMessage({ type: 'plugin:close' }, '*')
  );
})();
