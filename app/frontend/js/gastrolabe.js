/* Clockwise 90 degree view rotation; source x=cost and y=time never change. */
globalThis.MapGeometry = Object.freeze({
  position(x, y, view = null) {
    if (!view) return {left: (y + 100) / 2, top: (x + 100) / 2};
    return {left: 50 + 48 * (y - view.y) / view.radius, top: 50 + 48 * (x - view.x) / view.radius};
  },
  jitter(id) {
    // Stable visual offset only (at most 1.4% per axis); never change recipe data.
    let hash = 2166136261;
    for (const char of String(id)) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
    const left = ((hash >>> 0) % 1001 / 1000 - .5) * 2.8;
    hash = Math.imul(hash ^ (hash >>> 16), 2246822507);
    return {left, top: ((hash >>> 0) % 1001 / 1000 - .5) * 2.8};
  },
  view(points, user) {
    // A uniform camera fit only: source coordinates and Niche distances never change.
    const all = [...points, user];
    const x = (Math.min(...all.map(p => p.x)) + Math.max(...all.map(p => p.x))) / 2;
    const y = (Math.min(...all.map(p => p.y)) + Math.max(...all.map(p => p.y))) / 2;
    return {x, y, radius: Math.max(55, 1.12 * Math.max(...all.map(p => Math.hypot(p.x - x, p.y - y))))};
  },
  popup(anchor, bounds, size) {
    const gap = 16, pad = 8;
    const right = anchor.left + anchor.width + gap;
    const left = anchor.left - size.width - gap;
    const x = right + size.width <= bounds.width - pad ? right : left >= pad ? left : anchor.left + anchor.width / 2 - size.width / 2;
    const beside = right + size.width <= bounds.width - pad || left >= pad;
    const y = beside ? anchor.top - 30 : anchor.top + anchor.height + gap + size.height <= bounds.height - pad ? anchor.top + anchor.height + gap : anchor.top - size.height - gap;
    return {left: Math.max(pad, Math.min(x, bounds.width - size.width - pad)), top: Math.max(pad, Math.min(y, bounds.height - size.height - pad))};
  },
  distance(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); },
  brightness(a, b) { return Math.hypot(a.x - b.x, a.y - b.y) / Math.hypot(200, 200); },
  appearance(a, b, range = null) {
    const distance = Math.hypot(a.x - b.x, a.y - b.y) / Math.hypot(200, 200);
    // Fixed monotone contrast curve: the old exponential brightened nearby stars
    // too quickly. Keep the same Euclidean Niche, with a stronger visible midrange.
    const sigmoid = value => 1 / (1 + Math.exp(-value));
    const floor = sigmoid(-3.84), ceiling = sigmoid(8.16);
    // Full-axis normalization alone made a cluster of ordinary recipes all dim.
    // Stretch the visible distance range, without changing coordinates or Niche.
    const relative = range && range.max - range.min > 1
      ? (MapGeometry.distance(a, b) - range.min) / (range.max - range.min) : null;
    const light = relative === null
      ? Math.max(0, Math.min(1, (sigmoid(12 * (distance - .32)) - floor) / (ceiling - floor)))
      : Math.max(0, Math.min(1, relative)) ** 1.25;
    const halo = light ** 1.5;
    return {opacity: .22 + .78 * light, glow: 1 + 33 * halo,
      glowAlpha: .01 + .99 * halo, luminance: .6 + 1.5 * light, scale: 1 + distance};
  }
});


(() => {
  'use strict';
  const data = JSON.parse(document.getElementById('map-data').textContent);
  const {cosmos, previews, session} = data;
  const $ = id => document.getElementById(id);
  let profile = data.profile, previewId = null, selectedId = null;
  let profileVersion = 0;
  let mapView = null;
  const bridges = new Map(), pending = new Map();
  const notify = payload => parent !== window && parent.postMessage({...payload, session}, location.origin);
  let resizeFrame;
  function resize() {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      // flow-root contains child margins; measure intrinsic body size, not the
      // iframe viewport's scrollHeight, so the frame can shrink as well as grow.
      notify({type: 'gastrolabe:resize', height: Math.ceil(document.body.getBoundingClientRect().height) + 2});
    });
  }
  new ResizeObserver(resize).observe(document.body);
  window.addEventListener('load', resize);
  notify({type: 'gastrolabe:ready'});
  async function api(path, body) {
    const response = await fetch('/gastrolabe/' + path + '?session=' + encodeURIComponent(session), {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: AbortSignal.timeout(30000)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message || 'Please try again in a moment.');
    return result;
  }
  function place(element, point) {
    const p = MapGeometry.position(point.x, point.y, mapView);
    if (element.classList.contains('recipe-point')) {
      const offset = MapGeometry.jitter(point.id);
      p.left += offset.left; p.top += offset.top;
    }
    element.style.left = p.left + '%'; element.style.top = p.top + '%';
  }
  function showMap(reveal = true) {
    $('gateway').hidden = true; $('map-section').hidden = !reveal;
    $('cancel-edit').hidden = !reveal;
    const user = {x: profile.user_x, y: profile.user_y};
    closePreview(false);
    mapView = MapGeometry.view(cosmos, user);
    place($('zenith'), user);
    $('sky').querySelectorAll('.recipe-point').forEach(p => p.remove());
    const distances = cosmos.map(star => MapGeometry.distance(star, user));
    const lightRange = {min: Math.min(...distances), max: Math.max(...distances)};
    cosmos.forEach(star => {
      const point = document.createElement('button'); point.type = 'button'; point.className = 'recipe-point';
      if ((previews[String(star.id)]?.missing_count || 0) > 0) point.classList.add('recipe-needs-ingredients');
      point.dataset.id = star.id; point.setAttribute('aria-label', star.topic); point.setAttribute('aria-pressed', 'false');
      point.setAttribute('aria-controls', 'recipe-preview'); point.setAttribute('aria-expanded', 'false');
      place(point, star);
      const light = MapGeometry.appearance(star, user, lightRange);
      point.style.setProperty('--strength', String(light.opacity));
      point.style.setProperty('--glow', light.glow + 'px');
      point.style.setProperty('--glow-alpha', String(light.glowAlpha));
      point.style.setProperty('--luminance', String(light.luminance));
      point.style.setProperty('--star-scale', String(light.scale));
      point.onclick = event => {
        if (selectedId === star.id && !$('recipe-preview').hidden) { closePreview(false); return; }
        select(star);
        if (event.detail === 0) $('preview-close').focus();
      };
      $('map-plot').append(point);
    });
    resize();
  }
  function preview(star) {
    previewId = star.id;
    const meta = previews[String(star.id)] || {};
    $('recipe-preview').hidden = false; $('preview-content').hidden = false;
    $('preview-title').textContent = star.topic;
    $('preview-meta').textContent = `≈ ${meta.time ?? '—'} min · ≈ £${Number(meta.price || 0).toFixed(2)} / serving`;
    $('preview-missing').textContent = meta.missing_count > 0
      ? `${meta.missing_essential_count || 0} essential · ${meta.missing_count} total ingredients missing`
      : 'Uses only your confirmed ingredients';
    $('preview-source').textContent = meta.source_name === 'Gemini' ? 'Recipe created by Gemini from your ingredients · photo of a similar dish' : 'Recipe & photo: TheMealDB';
    const img = $('preview-image'); img.hidden = true; $('preview-fallback').hidden = false;
    img.onload = () => { if (previewId === star.id) { img.hidden = false; $('preview-fallback').hidden = true; } };
    img.onerror = () => { img.hidden = true; $('preview-fallback').hidden = false; };
    img.alt = star.topic;
    try { const url = new URL(meta.image_url); if (url.protocol !== 'https:') throw new Error(); img.src = url.href; }
    catch { img.removeAttribute('src'); }
    $('bridge-text').textContent = bridges.get(star.id) || (pending.has(star.id) ? 'Finding a connection to your cooking style…' : 'Select this recipe to discover what makes it a new experience for you.');
    $('bridge-retry').hidden = true;
    const selected = selectedId === star.id;
    $('preview-action').textContent = selected ? 'View ingredients →' : 'Explore this recipe →';
    $('preview-action').onclick = () => selectedId === star.id ? continueToRecipe(star) : select(star);
    positionPopup();
    resize();
  }
  function positionPopup() {
    const popup = $('recipe-preview');
    if (popup.hidden || selectedId === null) return;
    const point = $('sky').querySelector(`[data-id="${selectedId}"]`);
    if (!point) return;
    const bounds = popup.parentElement.getBoundingClientRect(), anchor = point.getBoundingClientRect();
    popup.style.maxHeight = Math.max(100, bounds.height - 16) + 'px';
    const size = popup.getBoundingClientRect();
    const position = MapGeometry.popup({left: anchor.left - bounds.left, top: anchor.top - bounds.top, width: anchor.width, height: anchor.height}, bounds, size);
    popup.style.left = Math.round(position.left) + 'px'; popup.style.top = Math.round(position.top) + 'px';
  }
  function closePreview(restoreFocus = true) {
    const point = $('sky').querySelector(`[data-id="${selectedId}"]`);
    $('recipe-preview').hidden = true; $('preview-content').hidden = true;
    $('sky').querySelectorAll('.recipe-point').forEach(p => { p.setAttribute('aria-expanded', 'false'); p.setAttribute('aria-pressed', 'false'); });
    selectedId = null; previewId = null;
    if (restoreFocus) point?.focus();
  }
  $('preview-close').onclick = () => closePreview();
  document.addEventListener('click', event => {
    if (!$('recipe-preview').hidden && !$('recipe-preview').contains(event.target) && !event.target.closest('.recipe-point')) closePreview(false);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !$('recipe-preview').hidden) { event.preventDefault(); closePreview(); }
  });
  window.addEventListener('resize', positionPopup);
  new ResizeObserver(positionPopup).observe($('recipe-preview'));
  async function introduction(star) {
    const version = profileVersion;
    if (bridges.has(star.id)) return;
    if (!pending.has(star.id)) pending.set(star.id, api('empathy_bridge', {id: star.id}));
    try {
      const response = await pending.get(star.id);
      if (version !== profileVersion) return;
      bridges.set(star.id, response.bridge_text);
      if (previewId === star.id) $('bridge-text').textContent = response.bridge_text;
    } catch {
      if (version === profileVersion && previewId === star.id) {
        $('bridge-text').textContent = 'Your personal introduction is unavailable. You can still view the recipe and ingredients.';
        $('bridge-retry').hidden = false; $('bridge-retry').onclick = () => { $('bridge-retry').hidden = true; introduction(star); };
      }
    } finally { if (version === profileVersion) pending.delete(star.id); resize(); }
  }
  function select(star) {
    selectedId = star.id; preview(star);
    $('sky').querySelectorAll('.recipe-point').forEach(p => {
      const selected = String(Number(p.dataset.id) === star.id);
      p.setAttribute('aria-pressed', selected); p.setAttribute('aria-expanded', selected);
    });
    if (!bridges.has(star.id)) $('bridge-text').textContent = 'Finding a connection to your cooking style…';
    introduction(star);
  }
  function continueToRecipe(star) {
    if (parent !== window) notify({type:'gastrolabe:select', id:star.id});
    else location.href = '/?map_session=' + encodeURIComponent(session) + '&recipe_id=' + star.id;
  }
  const transitionKey = `gastrolabe:${session}:transition`;
  let busy = false, controller;
  const save = record => sessionStorage.setItem(transitionKey, JSON.stringify(record));
  const sameAnswers = (a, b) => a && b && [1,2,3].every(i => a['ans'+i] === b['ans'+i]);
  function savedTransition() {
    try {
      const record = JSON.parse(sessionStorage.getItem(transitionKey));
      return record?.version === 1 && [1,2,3].every(i => typeof record.answers?.['ans'+i] === 'string' && record.answers['ans'+i].trim()) ? record : null;
    } catch { return null; }
  }
  async function recover(record, signal) {
    if (record.result) return record.result;
    if (record.status === 'error') throw new Error(record.error);
    if (sameAnswers(data.profile?.answers, record.answers)) return data.profile;
    // A refresh must not submit another AI request. Recover the original server result.
    while (Date.now() < record.startedAt + 40000) {
      signal.throwIfAborted();
      const response = await fetch('/gastrolabe/state?session=' + encodeURIComponent(session), {signal: AbortSignal.any([signal, AbortSignal.timeout(5000)])});
      if (!response.ok) throw new Error('Could not restore your map. Please retry.');
      const state = await response.json();
      if (sameAnswers(state.profile?.answers, record.answers)) return state.profile;
      await WorldLoading.wait(1000, signal);
    }
    throw new Error('Your previous request did not finish. Please retry.');
  }
  async function runTransition(answers, existing = null) {
    if (busy) return;
    busy = true; controller = new AbortController();
    const signal = controller.signal, button = $('ignite-btn'), loader = $('zenith-loading');
    const record = existing || {version:1, answers, startedAt:Date.now(), status:'waiting', animationDone:false};
    save(record);
    button.disabled = true; button.textContent = 'Finding your starting point…'; $('gateway-error').hidden = true;
    $('gateway').hidden = true; $('map-section').hidden = true; $('cancel-edit').hidden = true;
    loader.classList.remove('world-leaving'); loader.hidden = false;
    loader.querySelector('.lead').textContent = 'Exploring your culinary world…'; resize();
    const animation = (record.animationDone ? Promise.resolve() : WorldLoading.playOnce(loader, signal)).then(() => {
      record.animationDone = true; save(record);
      loader.querySelector('.lead').textContent = 'Finishing your map…';
    });
    const work = (existing ? recover(record, signal) : api('calculate_zenith', answers)).then(result => {
      record.result = result; record.status = 'ready'; save(record);
      signal.throwIfAborted();
      profile = {...result, answers}; profileVersion++; bridges.clear(); pending.clear(); selectedId = null;
      showMap(false);
      return result;
    }).catch(error => {
      if (!signal.aborted) { record.status = 'error'; record.error = error.message; save(record); }
      throw error;
    });
    try {
      await WorldLoading.waitForBoth(animation, work);
      signal.throwIfAborted();
      if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
        loader.classList.add('world-leaving'); await WorldLoading.wait(180, signal);
      }
      loader.hidden = true; $('map-section').hidden = false;
      $('cancel-edit').hidden = false;
      $('map-section').classList.add('world-reveal'); sessionStorage.removeItem(transitionKey);
      button.textContent = 'Find my cooking world →';
    } catch (error) {
      if (signal.aborted) return;
      loader.hidden = true; $('map-section').hidden = true; $('gateway').hidden = false;
      $('gateway-error').textContent = error.message; $('gateway-error').hidden = false;
      button.textContent = 'Try again →';
    } finally {
      if (!signal.aborted) { busy = false; button.disabled = false; loader.classList.remove('world-leaving'); resize(); }
    }
  }
  $('gateway-form').onsubmit = event => {
    event.preventDefault(); if (busy) return;
    const answers = Object.fromEntries([1,2,3].map(i => ['ans'+i, $('q'+i).value.trim()]));
    if (Object.values(answers).some(value => !value)) {
      $('gateway-error').textContent = 'Please add a short answer to each question.'; $('gateway-error').hidden = false; return;
    }
    const previous = savedTransition();
    // If only the asset failed, retry playback using the already-computed Zenith.
    runTransition(answers, previous?.result && sameAnswers(previous.answers, answers) ? previous : null);
  };
  window.addEventListener('pagehide', () => controller?.abort());
  window.addEventListener('pageshow', event => { if (event.persisted && busy) location.reload(); });
  // Download only: no display, playback or AI call before questionnaire submission.
  WorldLoading.prepare().catch(() => {});
  $('cancel-edit').onclick = showMap;
  [1,2,3].forEach(i => { const input = $('q'+i), key = `gastrolabe:${session}:q${i}`; input.value = sessionStorage.getItem(key) || profile?.answers?.['ans'+i] || ''; input.oninput = () => sessionStorage.setItem(key, input.value); });
  const unfinished = savedTransition();
  if (unfinished) runTransition(unfinished.answers, unfinished);
  else if (profile) showMap();
})();
