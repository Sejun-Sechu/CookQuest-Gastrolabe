/* Metadata boundary for Gastrolabe map discovery and selection. */
(() => {
  const el = (tag, cls, text) => {
    const e = document.createElement(tag); if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text; return e;
  };
  let renderer = mapRenderer;
  let lastRender;
  let axes;
  let batch;
  let activeFrame;
  let frameStatus;
  let frameTimer;
  let frameReady;
  function remember(data) {
    if (data.map_session_id !== localStorage.getItem('cookquest.map.session')) sessionStorage.removeItem('cookquest.map.selected');
    batch = data;
    if (data.map_session_id) localStorage.setItem('cookquest.map.session', data.map_session_id);
  }
  function mapRenderer(context) {
    const {container} = context;
    clearTimeout(frameTimer); activeFrame = null;
    if (!batch?.map_url) {
      container.replaceChildren(el('p', 'hint', 'No recipe map is available. Return to your ingredients and try again.'));
      return;
    }
    container.replaceChildren(); container.classList.add('gastrolabe-host');
    const toolbar = el('div', 'gastrolabe-toolbar');
    const note = el('p', 'hint', 'Loading your recipe map…');
    note.setAttribute('role', 'status'); frameStatus = note;
    const content = el('div', 'gastrolabe-content');
    const loading = el('p', 'hint', 'Preparing your recipe map…'); loading.setAttribute('role','status');
    const retry = el('button','btn','Retry map loading'); retry.type = 'button'; retry.hidden = true;
    retry.onclick = () => mapRenderer(context);
    const frame = el('iframe', 'gastrolabe-frame');
    frame.hidden = true;
    frame.src = batch.map_url; frame.title = 'Gastrolabe Gateway and 2D Recipe Map';
    frame.setAttribute('referrerpolicy', 'same-origin');
    activeFrame = frame;
    let settled = false;
    function fail() {
      if (activeFrame !== frame || settled) return;
      settled = true; clearTimeout(frameTimer); loading.hidden = true; retry.hidden = false;
      note.textContent = 'The map could not finish loading. Please retry loading the map.';
    }
    frameReady = () => {
      if (activeFrame !== frame || settled) return;
      settled = true; clearTimeout(frameTimer);
      loading.hidden = true; frame.hidden = false; frame.classList.add('world-reveal'); note.textContent = '';
    };
    clearTimeout(frameTimer);
    frameTimer = setTimeout(fail, 15000);
    frame.addEventListener('error', fail);
    toolbar.append(retry, note); content.append(loading, frame); container.append(toolbar, content);
  }
  async function select(id) {
    if (!batch?.map_session_id || !Number.isInteger(id)) return;
    const token = batch.map_session_id;
    const response = await fetch(`/gastrolabe/selection/${id}?session=${encodeURIComponent(token)}`, {signal: AbortSignal.timeout(15000)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || 'Recipe unavailable.');
    if (batch.map_session_id !== token) return;
    sessionStorage.setItem('cookquest.map.selected', String(id));
    lastRender?.onSelect(data.recipe);
  }
  window.addEventListener('message', async event => {
    if (event.origin !== location.origin || event.source !== activeFrame?.contentWindow ||
        event.data?.session !== batch?.map_session_id) return;
    if (event.data.type === 'gastrolabe:ready') {
      frameReady?.(); return;
    }
    if (event.data.type === 'gastrolabe:resize') {
      const height = event.data.height;
      if (Number.isFinite(height)) {
        const nextHeight = Math.min(4000, Math.max(320, Math.ceil(height))) + 'px';
        if (activeFrame.style.height !== nextHeight) activeFrame.style.height = nextHeight;
      }
      return;
    }
    if (event.data.type === 'gastrolabe:focus-preview') {
      if (Number.isFinite(event.data.offset)) window.scrollTo({top: scrollY + activeFrame.getBoundingClientRect().top + Math.max(0,event.data.offset) - 20, behavior:'smooth'});
      return;
    }
    if (event.data.type !== 'gastrolabe:select') return;
    try { await select(event.data.id); }
    catch (error) { if (frameStatus) frameStatus.textContent = error.message; }
  });
  window.RecipeExplorer = {
    async discover(inventory, refresh = false) {
      const response = await fetch("/api/v1/integration/recipes/discover", {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({...inventory, refresh}), signal: AbortSignal.timeout(120000),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || "Recipe discovery is unavailable. Please retry.");
      axes = data.axis_definitions;
      remember(data);
      window.dispatchEvent(new CustomEvent("cookquest:recipes", {detail: data}));
      return data;
    },
    async restore(token) {
      if (!/^[a-f0-9]{32}$/.test(token || '')) throw new Error('Saved map is invalid. Start a new collection.');
      const response = await fetch('/gastrolabe/state?session=' + encodeURIComponent(token), {signal: AbortSignal.timeout(15000)});
      if (!response.ok) throw new Error('Saved map unavailable. Scan your fridge to start again.');
      const data = await response.json(); remember(data); return data;
    },
    select,
    render(recipes, container, onSelect) {
      lastRender = {recipes, container, onSelect, axisDefinitions: axes};
      renderer(lastRender);
    },
    setRenderer(nextRenderer) {
      renderer = nextRenderer || mapRenderer;
      if (lastRender) renderer(lastRender);
    },
  };
})();
