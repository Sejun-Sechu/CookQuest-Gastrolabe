"use strict";

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  liveAI: false,        // server has an API key
  demo: false,          // demo mode on (forced when liveAI is false)
  photos: [],           // [{ dataUrl, base64 }] waiting to be analysed
  photosAnalysed: 0,
  // Detection result in the server's format; the player edits it in place.
  // detected_items: [{ id, name, detected_name, brand, category, quantity,
  //                    quantity_is_certain, confidence, needs_confirmation, evidence, user_confirmed }]
  // unknown_items:  [{ id, description, confidence }]
  detection: null,
  pantryOptions: [],
  pantry: new Set(),
  recipe: null,
};
let localId = 1;

const DEMO_PANTRY = ["salt", "black pepper", "olive oil"];
const STEP_OF = { scan: "scan", review: "review", more: "review", loading: null, options: "recipe", recipe: "recipe" };
const STEP_ORDER = ["scan", "review", "recipe"];
STEP_OF.home = null;
STEP_OF.find = "recipe";
STEP_OF.restaurant = null;
let recipeEntry = "fridge";
let catalog = [];
const DETECT_TIMEOUT_MS = 110000;   // server stops detection at 90 s; this covers upload + slack
const RECIPE_TIMEOUT_MS = 90000;
const PHOTO_MAX_EDGE = 3200;        // server keeps this for zoom crops; model sees <= 2576 px

const $ = (id) => document.getElementById(id);
const API_VERSION = 3;              // must match server.py API_VERSION

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
let currentScreen = "home";
function show(name, recordHistory = true) {
  if (!document.getElementById('screen-' + name)) name = 'home';
  document.body.classList.toggle('is-home', name === 'home');
  document.documentElement.classList.toggle('map-view', name === 'options' || name === 'find');
  if (recordHistory && currentScreen !== name && name !== "loading") {
    history.pushState({cookquestScreen: name}, "");
  }
  document.querySelectorAll(".screen").forEach((s) => (s.hidden = s.id !== "screen-" + name));
  hideError();
  document.querySelector(".steps").hidden = ["home", "find", "restaurant"].includes(name);
  const step = STEP_OF[name];
  if (step) {
    const idx = STEP_ORDER.indexOf(step);
    document.querySelectorAll(".steps li").forEach((li, i) => {
      li.classList.toggle("done", i < idx);
      li.classList.toggle("current", i === idx);
    });
  }
  currentScreen = name;
  sessionStorage.setItem("cookquest.map.view", name);
  window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
}
history.replaceState({cookquestScreen: "home"}, "");
window.addEventListener("popstate", event => {
  const target = event.state?.cookquestScreen || "home";
  if (target === "recipe" && !state.recipe) show(state.options?.length ? "options" : "home", false);
  else if (target === "options" && !state.options?.length) show("home", false);
  else show(target, false);
});

let loadingTimer = null;
function loading(steps) {
  // Update progress messages internally without displaying a countdown/deadline.
  const msgs = Array.isArray(steps) ? steps : [[0, steps]];
  const started = Date.now();
  const tick = () => {
    const s = Math.floor((Date.now() - started) / 1000);
    const msg = msgs.filter(([at]) => at <= s).pop()[1];
    $("loading-text").textContent = msg;
  };
  clearInterval(loadingTimer);
  tick();
  if (msgs.length > 1) loadingTimer = setInterval(tick, 1000);
  show("loading");
}
function stopLoading() { clearInterval(loadingTimer); loadingTimer = null; }

let retryAction = null;
let demoAction = null;
let retryTimer = null;
// retry: action for the Retry button (null hides it). retryAfter (seconds): keep Retry disabled
// with a countdown until the rate-limit window resets. continueInDemo: what "Use demo mode"
// re-runs (defaults to retry), so demo mode works even when retrying can't help (daily quota).
function showError(message, offerDemo, returnTo, retry, retryAfter, continueInDemo) {
  stopLoading();
  if (returnTo) show(returnTo);
  retryAction = retry || null;
  demoAction = continueInDemo || retry || null;
  $("error-text").textContent = message;
  const btn = $("btn-error-retry");
  btn.hidden = !retry;
  clearInterval(retryTimer);
  btn.disabled = false;
  btn.textContent = "Retry";
  if (retry && retryAfter > 0) {
    const until = Date.now() + retryAfter * 1000;
    const tick = () => {
      const left = Math.ceil((until - Date.now()) / 1000);
      if (left <= 0) { clearInterval(retryTimer); btn.disabled = false; btn.textContent = "Retry"; return; }
      btn.disabled = true;
      btn.textContent = `Retry in ${left}s`;
    };
    tick();
    retryTimer = setInterval(tick, 500);
  }
  $("btn-error-demo").hidden = true;
  $("error-box").hidden = false;
  $("error-box").scrollIntoView({ behavior: "smooth", block: "center" });
}
function hideError() { clearInterval(retryTimer); $("error-box").hidden = true; }

// ---------------------------------------------------------------------------
// Demo mode
// ---------------------------------------------------------------------------
function setDemo(on) {
  state.demo = false; // Integration never silently substitutes sample data.
  $("demo-banner").hidden = !state.demo;
  $("demo-toggle").checked = state.demo;
  $("btn-sample").hidden = !state.demo;
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
async function api(path, body, timeoutMs) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs || 60000);
  let res;
  try {
    res = await fetch("/fridge" + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...body, demo: state.demo }),
      signal: ctrl.signal,
    });
  } catch (e) {
    clearTimeout(timer);
    if (e.name === "AbortError") {
      throw { code: "timeout", offerDemo: true,
              message: "The request took too long. Please retry." };
    }
    throw { code: "network", offerDemo: true,
            message: "Can't reach the game server. Check that it is still running and that your phone is on the same Wi-Fi." };
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    clearTimeout(timer);
    throw { code: "malformed", offerDemo: true,
            message: `The server sent an unreadable response (${res.status}). Retry later.` };
  }
  clearTimeout(timer);
  if (!res.ok) {
    let message = (data && data.error) || `Server error (${res.status}). Retry later.`;
    if (res.status === 413) message = "The photos are too large to upload together. Try one photo at a time.";
    throw { code: (data && data.code) || "server_error", message, offerDemo: !data || data.demo_available !== false,
            retryAfter: data && data.retry_after, quotaType: data && data.quota_type,
            errorType: data && data.error_type };
  }
  if (!data || typeof data !== "object") {
    throw { code: "malformed", offerDemo: true, message: "The server sent an empty response. Retry later." };
  }
  return data;
}

// ---------------------------------------------------------------------------
// Step 1: skill level
// ---------------------------------------------------------------------------
document.querySelectorAll("[data-back]").forEach((b) =>
  b.addEventListener("click", () => show(b.dataset.back))
);

// ---------------------------------------------------------------------------
// Step 2: photos
// ---------------------------------------------------------------------------
// Keep enough resolution for labels; the server downsizes for the model and
// crops from this version when the model zooms in.
function loadAndResize(fileOrUrl) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const isUrl = typeof fileOrUrl === "string";
    const src = isUrl ? fileOrUrl : URL.createObjectURL(fileOrUrl);
    img.onload = () => {
      const scale = Math.min(1, PHOTO_MAX_EDGE / Math.max(img.naturalWidth, img.naturalHeight));
      const w = Math.round(img.naturalWidth * scale) || 1200;
      const h = Math.round(img.naturalHeight * scale) || 1500;
      const canvas = document.createElement("canvas");
      canvas.width = w; canvas.height = h;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, w, h);
      ctx.drawImage(img, 0, 0, w, h);
      if (!isUrl) URL.revokeObjectURL(src);
      const dataUrl = canvas.toDataURL("image/jpeg", 0.9);
      resolve({ dataUrl, base64: dataUrl.split(",")[1] });
    };
    img.onerror = () => {
      if (!isUrl) URL.revokeObjectURL(src);
      reject(new Error("That file couldn't be opened as an image. Try a JPEG or PNG photo."));
    };
    img.src = src;
  });
}

async function readFiles(files) {
  const out = [];
  for (const f of [...files].slice(0, 4)) out.push(await loadAndResize(f));
  return out;
}

let photosLoading = false;
async function usePhotos(filesOrUrl) {
  if (photosLoading) return;
  photosLoading = true; $("btn-choose-photo").disabled = true;
  $("btn-analyze").disabled = true; $("btn-repick").disabled = true;
  try {
    const incoming = typeof filesOrUrl === "string" ? [await loadAndResize(filesOrUrl)] : await readFiles(filesOrUrl);
    // Accumulate separate picker selections before a single recognition request.
    state.photos = [...new Map([...state.photos, ...incoming].map(photo => [photo.base64, photo])).values()].slice(0, 4);
  } catch (e) {
    showError(e.message, false);
    return;
  } finally {
    photosLoading = false; $("btn-choose-photo").disabled = state.photos.length >= 4;
    $("btn-analyze").disabled = false; $("btn-repick").disabled = false;
    $("input-gallery").value = "";
  }
  if (!state.photos.length) return;
  $("photo-img").src = state.photos[0].dataUrl;
  $("photo-count").textContent = `${state.photos.length} / 4 photos selected. All selected photos will be analysed together.`;
  $("photo-thumbnails").replaceChildren();
  state.photos.slice(1).forEach((photo, index) => {
    const thumb = document.createElement("img"); thumb.src = photo.dataUrl; thumb.alt = `Fridge photo ${index + 2}`;
    $("photo-thumbnails").appendChild(thumb);
  });
  $("btn-choose-photo").textContent = state.photos.length >= 4 ? "4 photos selected" : "Add more photos";
  $("photo-preview").hidden = false;
  $("scan-actions").hidden = false;
  $("analyze-actions").hidden = false;
  hideError();
}

function resetPhotoPicker() {
  state.photos = [];
  $("photo-preview").hidden = true;
  $("scan-actions").hidden = false;
  $("analyze-actions").hidden = true;
  $("input-gallery").value = "";
  $("photo-thumbnails").replaceChildren(); $("photo-count").textContent = "";
  $("btn-choose-photo").textContent = "Add a photo"; $("btn-choose-photo").disabled = false;
}

$("btn-choose-photo").addEventListener("click", () => $("input-gallery").click());
$("input-gallery").addEventListener("change", (e) => {
  if (e.target.files && e.target.files.length) usePhotos(e.target.files);
});
$("btn-sample").addEventListener("click", () => usePhotos("/assets/demo-fridge.svg"));
$("btn-repick").addEventListener("click", resetPhotoPicker);

$("btn-manual").addEventListener("click", () => startManual());
function startManual(message) {
  state.detection = { image_quality: "good", needs_another_photo: false, photo_advice: "",
                      detected_items: [], unknown_items: [] };
  renderReview({ manual: true, message });
  show("review");
  $("ingredient-search").focus();
}

const DETECT_STEPS = [
  [0, "Uploading your photo…"],
  [3, "Scanning every shelf, drawer and door…"],
  [15, "Zooming in to read labels…"],
  [35, "Checking each item…"],
  [55, "Still working - Gemini's free tier can be slow…"],
];

// Errors that retrying cannot fix: no Retry button (demo mode is offered instead).
const NO_RETRY = new Set(["quota_daily", "no_free_quota", "billing_required", "invalid_key", "permission",
                          "model_unavailable", "bad_image", "too_small", "no_image", "too_many_photos"]);
let detectInFlight = false;
let minuteLimitStrikes = 0;                    // consecutive per-minute 429s -> exponential backoff

function setScanControls(enabled) {
  $("btn-analyze").disabled = !enabled;
  $("btn-choose-photo").disabled = !enabled || photosLoading || state.photos.length >= 4;
  $("input-gallery").disabled = !enabled;
}

async function detect(photos, previous) {
  if (detectInFlight) return;                  // one click = one scan; ignore double clicks
  detectInFlight = true;
  setScanControls(false);
  const retry = () => detect(photos, previous);
  const back = previous ? "review" : "scan";
  loading(state.demo ? "Loading sample ingredients…" : DETECT_STEPS);
  let data;
  try {
    data = await api("/api/detect", { images: photos.map((p) => p.base64), previous }, DETECT_TIMEOUT_MS);
    if (!Array.isArray(data.detected_items) || !Array.isArray(data.unknown_items)) {
      throw { code: "malformed", offerDemo: true, message: "The detection result was incomplete. Retry, or use demo mode." };
    }
  } catch (e) {
    let wait = 0;
    let message = e.message || "Something went wrong. Retry, or use demo mode.";
    if (e.code === "rate_limit_minute") {
      minuteLimitStrikes += 1;                 // 1st: Google's retry delay; then 2x, 4x... capped at 2 min
      wait = Math.min(120, Math.ceil(Math.max(1, e.retryAfter || 15) * 2 ** (minuteLimitStrikes - 1)));
      if (minuteLimitStrikes > 1) message += ` The limit was hit again, so Retry unlocks after ${wait} s.`;
    }
    const canRetry = !NO_RETRY.has(e.code) && e.code !== "duplicate_scan";
    showError(message, e.offerDemo !== false, back, canRetry ? retry : null, wait, retry);
    return;
  } finally {
    stopLoading();
    detectInFlight = false;
    setScanControls(true);
  }
  minuteLimitStrikes = 0;
  // Valid result: go straight to the Ingredients step.
  if (data.demo && !state.demo) setDemo(true);
  state.detection = data;
  state.photosAnalysed += photos.length;
  if (state.demo && state.pantry.size === 0) DEMO_PANTRY.forEach((p) => state.pantry.add(p));
  try {
    renderReview();
    show("review");
  } catch (err) {
    console.error(err);
    showError("Couldn't display the detection result. Retry, or use demo mode.", true, back, retry);
  }
}

$("btn-analyze").addEventListener("click", () => {
  if (!state.photos.length) return;
  state.photosAnalysed = 0;
  detect(state.photos, null);
});

// ---------------------------------------------------------------------------
// Step 3: review items
// ---------------------------------------------------------------------------
const items = () => (state.detection ? state.detection.detected_items : []);
const unknowns = () => (state.detection ? state.detection.unknown_items : []);
const isFlagged = (i) => i.needs_confirmation && !i.user_confirmed;

function renderReview({ manual, message } = {}) {
  state.detection.detected_items = window.IngredientInventory.merge(items());
  const d = state.detection;
  const warn = $("quality-warning");
  if (manual) {
    warn.hidden = true;
    $("detect-feedback").textContent = message || "Type what's in your fridge, one item at a time.";
  } else {
    const poor = d.image_quality === "poor";
    warn.hidden = !poor;
    if (poor) $("quality-text").textContent = "Some photos were hard to read. Review the items below or enter ingredients manually.";
    const n = items().length;
    $("detect-feedback").textContent = poor ? "" :
      `${n} item${n === 1 ? "" : "s"} found from ${state.photosAnalysed} photo${state.photosAnalysed === 1 ? "" : "s"}` +
      (d.image_quality === "acceptable" ? " (some areas were hard to see)." : ".");
  }
  // Uncertain items first so they get reviewed.
  items().sort((a, b) => Number(isFlagged(b)) - Number(isFlagged(a)));
  renderItems();
  renderUnknown();
}

function renderItems() {
  if (state.detection) sessionStorage.setItem('cookquest.inventory', JSON.stringify(state.detection));
  const list = $("ingredient-list");
  $("ingredient-count").textContent = `(${items().length})`;
  list.innerHTML = "";
  if (items().length === 0) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No identified items yet. Add ingredients below.";
    list.appendChild(li);
  }
  for (const it of items()) {
    const flagged = isFlagged(it);
    const li = document.createElement("li");
    li.className = "ing" + (flagged ? " flagged" : "");

    const main = document.createElement("div");
    main.className = "ing-main";
    const input = document.createElement("input");
    input.className = "ing-name";
    input.value = it.name;
    input.maxLength = 60;
    input.setAttribute("aria-label", "Item name (tap to rename)");
    input.addEventListener("change", () => {
      const v = window.IngredientInventory.canonical(input.value);
      if (!v) { removeItem(it.id); return; }
      it.name = v;
      it.user_confirmed = true; // renaming counts as confirming
      state.detection.detected_items = window.IngredientInventory.merge(items());
      renderItems();
    });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.blur(); });
    main.appendChild(input);

    if (flagged) main.appendChild(badge(it.confidence < 0.55 ? "low" : "medium", "Check"));

    const del = document.createElement("button");
    del.className = "icon-btn";
    del.type = "button";
    del.setAttribute("aria-label", `Remove ${it.name}`);
    del.textContent = "✕";
    del.addEventListener("click", () => removeItem(it.id));
    main.appendChild(del);
    li.appendChild(main);

    if (flagged) {
      const flag = document.createElement("div");
      flag.className = "ing-flag";
      const note = document.createElement("span");
      const why = (it.evidence || "Not sure about this one").replace(/[.\s]+$/, "");
      note.textContent = `⚠️ ${why}. Rename it, remove it, or confirm.`;
      const ok = document.createElement("button");
      ok.className = "btn small";
      ok.type = "button";
      ok.textContent = "✓ Confirm";
      ok.addEventListener("click", () => { it.user_confirmed = true; renderItems(); });
      flag.append(note, ok);
      li.appendChild(flag);
    }
    list.appendChild(li);
  }
  updateGenerateButton();
}

function renderUnknown() {
  const wrap = $("unknown-section");
  const list = $("unknown-list");
  list.innerHTML = "";
  wrap.hidden = unknowns().length === 0;
  for (const u of unknowns()) {
    const li = document.createElement("li");
    li.className = "ing";
    const p = document.createElement("div");
    p.className = "ing-meta";
    p.textContent = `❓ ${u.description}`;
    const form = document.createElement("form");
    form.className = "add-row";
    const input = document.createElement("input");
    input.placeholder = "What is it? (optional)";
    input.maxLength = 60;
    const add = document.createElement("button");
    add.className = "btn small";
    add.type = "submit";
    add.textContent = "Add";
    const dismiss = document.createElement("button");
    dismiss.className = "btn small";
    dismiss.type = "button";
    dismiss.textContent = "Ignore";
    form.append(input, add, dismiss);
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      if (addItem(input.value)) dropUnknown(u.id);
    });
    dismiss.addEventListener("click", () => dropUnknown(u.id));
    li.append(p, form);
    list.appendChild(li);
  }
}

function dropUnknown(id) {
  state.detection.unknown_items = unknowns().filter((u) => u.id !== id);
  renderUnknown();
  renderItems();
}

function badge(kind, text) {
  const b = document.createElement("span");
  b.className = "badge " + kind;
  b.textContent = text;
  return b;
}

function removeItem(id) {
  state.detection.detected_items = items().filter((i) => i.id !== id);
  renderItems();
}

function addItem(name) {
  const v = window.IngredientInventory.canonical(name).slice(0, 60);
  if (!v) return false;
  const existing = items().find((i) => window.IngredientInventory.canonical(i.name) === v);
  if (existing) { existing.user_confirmed = true; renderItems(); return true; }
  if (!state.detection) startManual();
  items().push({
    id: `usr_${localId++}`, name: v, detected_name: v, brand: null, category: "other",
    quantity: null, quantity_is_certain: false, confidence: 1, needs_confirmation: false,
    evidence: "", user_confirmed: true, added: true,
  });
  renderItems();
  return true;
}

window.IngredientInventory.autocomplete(
  $("ingredient-search"), $("ingredient-suggestions"),
  name => { if (addItem(name)) $("ingredient-selection-status").textContent = `${name} added to your ingredients.`; },
  () => items().map(item => item.name),
);

$("ingredient-add-form").addEventListener("submit", event => {
  event.preventDefault();
  const input = $("ingredient-search"), name = input.value.trim();
  if (!name) return;
  if (addItem(name)) {
    input.value = ""; input.focus();
    $("ingredient-selection-status").textContent = `${name} added to your ingredients.`;
  }
});

function updateGenerateButton() {
  const flagged = items().filter(isFlagged).length;
  const btn = $("btn-generate");
  const summary = $("flag-summary");
  summary.hidden = !flagged;
  if (flagged) {
    summary.textContent = `${flagged} item${flagged > 1 ? "s" : ""} need${flagged > 1 ? "" : "s"} your confirmation. Confirm, rename or remove ${flagged > 1 ? "them" : "it"} before creating your recipe.`;
  }
    btn.disabled = flagged > 0;
  btn.textContent = flagged
    ? `Confirm ${flagged} item${flagged > 1 ? "s" : ""} first`
      : items().length === 0 ? "Browse recipes anyway" : "Find matching recipes";
}

$("btn-quality-manual").addEventListener("click", () =>
  startManual("No problem - type what's in your fridge instead.")
);

// ---------------------------------------------------------------------------
// Recipe generation: uses ONLY the confirmed list (never the photos)
// ---------------------------------------------------------------------------
// Reads the server's {recipes: [...]} response. Throws a typed error instead of a generic message.
function parseRecipes(data) {
  if (data && !Array.isArray(data.recipes) && (data.dish_name || Array.isArray(data.options))) {
    throw { errorType: "SERVER_ERROR", code: "outdated_server", offerDemo: true,
            message: "The server is running an older version of the app. Stop it (Ctrl+C) and start it again with: python server.py" };
  }
  const list = (data && Array.isArray(data.recipes) ? data.recipes : [])
    .filter((r) => r && typeof r.dish_name === "string" && Array.isArray(r.ingredients));
  if (!list.length) {
    throw { errorType: "INVALID_RECIPE_RESPONSE", offerDemo: true, message: RECIPE_ERROR_TEXT.INVALID_RECIPE_RESPONSE };
  }
  return list;
}

const RECIPE_ERROR_TEXT = {
  RATE_LIMITED: "Gemini's free limit was reached. Wait for the countdown and retry, or use demo mode.",
  INVALID_API_KEY: "The server's Gemini API key isn't valid. Fix GEMINI_API_KEY in .env and restart the server, or use demo mode.",
  MODEL_UNAVAILABLE: "The configured Gemini model can't be used with this key. Change GEMINI_MODEL in .env, or use demo mode.",
  INVALID_RECIPE_RESPONSE: "Gemini's answer didn't contain usable recipes. Retry, or use demo mode.",
  NETWORK_TIMEOUT: "Creating recipes took too long or the connection failed. Retry, or use demo mode.",
  SERVER_ERROR: "Something went wrong on the server. Retry, or use demo mode.",
};

function recipeErrorType(code) {
  return ({ timeout: "NETWORK_TIMEOUT", network: "NETWORK_TIMEOUT", malformed: "INVALID_RECIPE_RESPONSE" })[code] || "SERVER_ERROR";
}

const NO_MATCH_TEXT = "No recipes fit your ingredients with at most two missing essentials. Update your list and try again.";

let recipeInFlight = false;
async function generate() {
  if (recipeInFlight) return;
  recipeInFlight = true;
  ['btn-generate','btn-regenerate-options'].forEach(id => $(id).disabled = true);
  loading("Discovering your culinary world…");
  recipeEntry = "fridge";
  try {
    const data = await window.RecipeExplorer.discover({
      ingredients: items().map((i) => i.name),
      pantry_staples: [],
    });
    stopLoading();
    if (data.status === "need_more_ingredients") {
      renderMore(data);
      show("more");
      return;
    }
    const list = Array.isArray(data.recipes) && data.recipes.length === 0 ? [] : parseRecipes(data);
    if (!list.length) {
      showError(NO_MATCH_TEXT, false, "review");
      return;
    }
    state.options = list;
    renderOptions(list);
    show("options");
  } catch (e) {
    const type = e.errorType || recipeErrorType(e.code);
    let wait = 0;
    if (type === "RATE_LIMITED" && e.quotaType === "per_minute") wait = Math.min(120, Math.max(1, e.retryAfter || 15));
    // Retry only helps with temporary problems; a daily quota, bad key or unavailable model won't fix itself.
    const temporary = ["NETWORK_TIMEOUT", "SERVER_ERROR", "INVALID_RECIPE_RESPONSE"].includes(type) ||
                      (type === "RATE_LIMITED" && e.quotaType !== "per_day");
    showError(e.message || RECIPE_ERROR_TEXT[type] || RECIPE_ERROR_TEXT.SERVER_ERROR, true, "review",
              temporary ? generate : null, wait, generate);
  } finally {
    stopLoading();
    recipeInFlight = false;
    ['btn-generate','btn-regenerate-options'].forEach(id => $(id).disabled = false);
  }
}

$("btn-generate").addEventListener("click", generate);
$("btn-regenerate").addEventListener("click", () => show(recipeEntry === "find" ? "find" : "review"));
$("btn-regenerate-options").addEventListener("click", generate);
$("btn-back-options").addEventListener("click", () => show(recipeEntry === "find" ? "find" : "options"));

function recipeStats(r) {
  const bits = [];
  if (typeof r.price === "number") bits.push(`≈ £${r.price.toFixed(2)}/serving · ≈ ${r.time} min`);
  if (typeof r.ingredient_match_percent === "number") bits.push(`${r.ingredient_match_percent}% ingredient match`);
  if (Array.isArray(r.ingredients) && r.ingredients.length) {
    const missing = r.ingredients.filter(i => !i.available).length;
    bits.push(missing ? `${missing} not on your confirmed list` : "Uses only your confirmed ingredients");
  }
  return bits.join(" · ");
}

// Missing or broken images fall back to the local placeholder; the recipe always stays.
function recipeImage(r, cls) {
  const icons = {"classic-omelette": "🍳", "classic-fried-rice": "🍚", "classic-aglio-olio": "🍝"};
  if (icons[r.id]) {
    const art = el("div", `${cls} catalog-art`, icons[r.id]);
    art.setAttribute("aria-hidden", "true");
    return art;
  }
  const img = el("img", cls);
  img.alt = r.image_source === "themealdb" ? `Photo of a similar dish: ${r.image_search_term || r.dish_name}` : "";
  img.loading = "lazy";
  img.src = r.image_url || "/assets/recipe-placeholder.svg";
  img.onerror = () => { img.onerror = null; img.src = "/assets/recipe-placeholder.svg"; };
  return img;
}

// Dietary badges such as Vegan or Vegetarian.
function dietBadges(r) {
  const wrap = el("div", "diet-badges");
  const badges = r.dietary_badges || (r.dietary_tags || []).map((t) => ({ tag: t, detail: "" }));
  for (const b of badges) {
    const cls = "diet-badge diet-" + b.tag.toLowerCase().replace(/[^a-z]+/g, "-");
    wrap.appendChild(el("span", cls, b.detail ? `${b.tag} · ${b.detail}` : b.tag));
  }
  return wrap;
}

// Cook gate: before the recipe opens for cooking, recheck the player's CURRENT confirmed list.
// A map or list may be older than the latest edits (e.g. chicken removed after discovery).
let cookInFlight = false;
async function cook(r) {
  if (cookInFlight) return;
  if (!r || typeof r.id !== "string" || !r.id) {
    showError("This recipe could not be opened. Select it again from the map, or refresh your matches.", false, currentScreen);
    return;
  }
  cookInFlight = true;
  const retry = () => cook(r);      // the map's Cook button stays usable; Retry repeats the same check
  const names = items().map((i) => String(i.name || "").trim()).filter(Boolean);
  let response, data = null;
  try {
    response = await fetch(`/api/v1/integration/recipes/${encodeURIComponent(r.id)}/check`, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ingredients: names, pantry_staples: []}),
      signal: AbortSignal.timeout(15000),
    });
    data = await response.json().catch(() => null);
  } catch (error) {
    cookInFlight = false;
    showError(error.name === "TimeoutError"
      ? "Checking your ingredients took too long. Please try Cook again."
      : "Could not reach the server to check your ingredients. Check that the app is still running, then try Cook again.",
      false, currentScreen, retry);
    return;
  }
  cookInFlight = false;
  const error = data?.error || {};
  if (response.ok && data?.ready === true) {             // 200: every ingredient is confirmed
    state.recipe = r;
    renderRecipe(r);
    show("recipe");
    return;
  }
  if (response.status === 409 && error.code === "ingredients_missing") {
    const missing = Array.isArray(error.missing) ? error.missing.join(", ") : "";
    showError(error.message || `Missing from your ingredient list: ${missing}. Update your ingredient list and try again.`,
              false, "review");                            // ingredients screen: edit the list there
    return;
  }
  if (response.status === 404 && error.code === "recipe_expired") {
    showError("This recipe is no longer saved. Select it again from the map, or use Refresh matches to search again.",
              false, currentScreen);
    return;
  }
  if (response.status === 404 || response.status === 405) { // route missing: an older server process is running
    showError("The server is running an older version of the app. Stop it (Ctrl+C), start it again with start.cmd, then reload this page.",
              false, currentScreen);
    return;
  }
  if (response.status === 422) {
    showError("Your ingredient list could not be checked. Keep it to 40 ingredients or fewer, then try Cook again.",
              false, "review");
    return;
  }
  showError(response.ok
      ? "The server sent an unexpected reply while checking your ingredients. Please try Cook again."
      : `The server had a problem checking your ingredients (error ${response.status}). Please try Cook again.`,
    false, currentScreen, retry);
}

function renderOptions(options, target = "option-list") {
  window.RecipeExplorer.render(options, $(target), cook);
}

// Restoring a saved map keeps the player's latest confirmed list (edited after discovery),
// falling back to the inventory the map was made from.
function restoredInventory(data) {
  try {
    const saved = JSON.parse(sessionStorage.getItem("cookquest.inventory"));
    if (Array.isArray(saved?.detected_items)) return saved;
  } catch {}
  return {detected_items: data.inventory.map(name => ({id: localId++, name, user_confirmed: true})), unknown_items: []};
}

function renderMore(data) {
  $("more-question").textContent = data.follow_up_question;
  const wrap = $("more-chips");
  wrap.innerHTML = "";
  const have = new Set(items().map((i) => i.name));
  for (const s of data.suggested_additions || []) {
    const c = document.createElement("button");
    c.type = "button";
    c.className = "chip";
    c.textContent = s;
    c.setAttribute("aria-pressed", have.has(s.toLowerCase()) ? "true" : "false");
    c.addEventListener("click", () => {
      const pressed = c.getAttribute("aria-pressed") === "true";
      if (pressed) {
        state.detection.detected_items = items().filter((i) => i.name !== s.toLowerCase());
        renderItems();
      } else {
        addItem(s);
      }
      c.setAttribute("aria-pressed", pressed ? "false" : "true");
    });
    wrap.appendChild(c);
  }
}

$("more-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const v = $("more-input").value;
  if (addItem(v)) {
    const c = document.createElement("button");
    c.type = "button"; c.className = "chip"; c.textContent = v.trim();
    c.setAttribute("aria-pressed", "true");
    c.disabled = true;
    $("more-chips").appendChild(c);
  }
  $("more-input").value = "";
});
$("btn-more-generate").addEventListener("click", generate);

// ---------------------------------------------------------------------------
// Step 4: recipe card
// ---------------------------------------------------------------------------
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

const recipeMethods = new Map();
async function renderMethod(recipe, section) {
  section.replaceChildren(el("h2", null, "How to make it"));
  const status = el("p", "hint", "Loading the recipe steps…");
  status.setAttribute("role", "status"); section.appendChild(status);
  try {
    if (!recipeMethods.has(recipe.id)) {
      const request = fetch(`/api/v1/integration/recipes/${encodeURIComponent(recipe.id)}/method`, {signal: AbortSignal.timeout(15000)})
        .then(async response => {
          const data = await response.json();
          if (!response.ok) throw new Error(data.error?.message || "Recipe steps are unavailable.");
          return data;
        }).catch(error => { recipeMethods.delete(recipe.id); throw error; });
      recipeMethods.set(recipe.id, request);
    }
    const method = await recipeMethods.get(recipe.id);
    if (!section.isConnected) return;
    status.remove();
    section.appendChild(el("p", "hint", recipe.source_name === "Gemini"
      ? "Check missing ingredients before following these steps. No photos or scoring required."
      : "Instructions from the original recipe. Follow these at your own pace—no photos or scoring required."));
    const list = el("ol", "recipe-method");
    method.steps.forEach(text => { const item = el("li"); item.style.whiteSpace = "pre-line"; item.appendChild(el("p", null, text)); list.appendChild(item); });
    section.appendChild(list);
  } catch (error) {
    if (!section.isConnected) return;
    status.textContent = error.name === "TimeoutError" ? "Recipe steps took too long to load. Please retry." : error.message;
    const retry = el("button", "btn small", "Retry steps"); retry.type = "button";
    retry.onclick = () => renderMethod(recipe, section); section.appendChild(retry);
  }
}

function renderRecipe(r) {
  const card = $("recipe-card");
  card.innerHTML = "";

  const head = el("div", "rc-head");
  head.appendChild(el("span", "rc-tag", "A new cooking discovery"));
  if (r.demo) head.appendChild(el("span", "rc-tag demo", "Demo sample"));
  head.prepend(recipeImage(r, "rc-img"));
  head.appendChild(el("h1", null, r.dish_name));
  head.appendChild(dietBadges(r));
  const stats = recipeStats(r);
  if (stats) head.appendChild(el("p", "diet-note recipe-stats", stats));
  if (r.dietary_note) head.appendChild(el("p", "diet-note", r.dietary_note));
  if (r.description) head.appendChild(el("p", null, r.description));
  if (r.source_url) {
    const source = el("a", "source-label", `${r.source_name || "Recipe source"} · ${r.source_author || "Original recipe"} ↗`);
    source.href = r.source_url; source.target = "_blank"; source.rel = "noopener noreferrer";
    head.append(source, el("p", "hint", r.source_note || "Published-source adaptation; not independently kitchen-tested."));
  }
  card.appendChild(head);

  const meta = el("div", "rc-meta");
  const total = r.time || (r.prep_minutes || 0) + (r.cook_minutes || 0);
  [[r.servings, "estimated servings"], [`≈ ${total} min`, "estimated total time"], [`≈ £${r.price?.toFixed(2) || "?"}`, "estimated per serving"]].forEach(([v, l]) => {
    const d = el("div");
    d.append(el("strong", null, String(v)), el("span", null, l));
    meta.appendChild(d);
  });
  card.appendChild(meta);

  const ingSec = el("section", "rc-section");
  ingSec.appendChild(el("h2", null, "Ingredients"));
  ingSec.appendChild(el("p", "hint", "Up to two essential ingredients may be missing. Check the missing items and quantities before cooking."));
  const ul = el("ul", "rc-ings");
  for (const i of r.ingredients) {
    const li = el("li", i.available ? "ingredient-have" : "ingredient-missing");
    const check = el("input"); check.type = "checkbox"; check.checked = i.available === true;
    check.setAttribute("aria-label", `I have ${i.name}`);
    check.onchange = () => {
      i.available = check.checked;
      const canonical = i.canonical_name || window.IngredientInventory.canonical(i.name);
      r.ingredients.forEach(item => { if ((item.canonical_name || window.IngredientInventory.canonical(item.name)) === canonical) item.available = check.checked; });
      li.className = i.available ? "ingredient-have" : "ingredient-missing";
      availability.textContent = `${i.available ? "Have" : "Missing / unconfirmed"} · ${i.role || "essential"}`;
      r.available_count = r.ingredients.filter(item => item.available).length;
      r.missing_count = r.ingredients.length - r.available_count;
      r.missing_essential_count = new Set(r.ingredients.filter(item => !item.available && item.role === "essential").map(item => item.canonical_name || item.name)).size;
      r.missing_supporting_count = new Set(r.ingredients.filter(item => !item.available && ["supporting", "optional"].includes(item.role)).map(item => item.canonical_name || item.name)).size;
      r.missing_uncertain_count = new Set(r.ingredients.filter(item => !item.available && item.role === "uncertain").map(item => item.canonical_name || item.name)).size;
      r.ingredient_match_percent = Math.round(100 * r.available_count / r.ingredients.length);
      card.querySelector(".recipe-stats").textContent = recipeStats(r);
      renderRecipe(r);
    };
    const name = el("span", null, i.name);
    const availability = el("small", "availability-label", `${i.available ? "Have" : "Missing / unconfirmed"} · ${i.role || "essential"}`);
    name.prepend(check); name.appendChild(availability);
    if (i.source === "pantry") name.appendChild(el("span", "src", "pantry"));
    li.append(name, el("span", "qty", i.quantity));
    ul.appendChild(li);
  }
  ingSec.appendChild(ul);
  if ((r.optional_ingredients || []).length) {
    ingSec.appendChild(el("p", "rc-note", `Optional (you don't have these): ${r.optional_ingredients.join(", ")}`));
  }
  ingSec.appendChild(el("p", "rc-note", "Quantities are what this recipe needs. Check you have enough before you start."));
  card.appendChild(ingSec);

  const methodSection = el("section", "rc-section");
  card.appendChild(methodSection); renderMethod(r, methodSection);
  const source = el("a", "btn primary", "View original recipe ↗");
  try {
    const url = new URL(r.source_url);
    if (url.protocol === "https:") {
      source.href = url.href; source.target = "_blank"; source.rel = "noopener noreferrer";
      card.appendChild(source);
    }
  } catch { /* Ingredient review still works without an external source. */ }
}

$("btn-restart").addEventListener("click", () => {
  state.detection = null;
  state.recipe = null;
  state.photosAnalysed = 0;
  state.pantry.clear();
  resetPhotoPicker();
  show("home");
});

// ---------------------------------------------------------------------------
// Errors + demo toggle
// ---------------------------------------------------------------------------
$("btn-error-dismiss").addEventListener("click", hideError);
$("btn-error-retry").addEventListener("click", () => {
  const fn = retryAction;
  hideError();
  if (fn) fn();
});
$("btn-error-demo").addEventListener("click", () => {
  const fn = demoAction;
  setDemo(true);
  hideError();
  DEMO_PANTRY.forEach((p) => state.pantry.add(p));
  if (fn) fn();                                 // re-run the failed step with demo data
  else if (currentScreen === "review") renderReview();
});
$("demo-toggle").addEventListener("change", (e) => setDemo(e.target.checked));

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
(async function init() {
  const resumeView = sessionStorage.getItem("cookquest.map.view");
  try { await window.IngredientInventory.load(); } catch (error) { $("ingredient-selection-status").textContent = error.message; }
  try {
    const res = await fetch("/fridge/api/status", {signal: AbortSignal.timeout(8000)});
    const s = await res.json();
    state.liveAI = !!s.live_ai;
    if (s.api_version !== API_VERSION) {
      showError("The server is running an older version of the app. Stop it (Ctrl+C) and start it again with: python server.py", true);
    }
    state.pantryOptions = s.pantry_staples || [];
  } catch {
    state.liveAI = false;
  }
  $("demo-toggle-wrap").hidden = true;
  setDemo(new URLSearchParams(location.search).has("demo"));
  if (currentScreen === "home") show("home", false);
  const params = new URLSearchParams(location.search);
  const token = params.get("map_session") || localStorage.getItem("cookquest.map.session");
  if (currentScreen === "home" && token && (params.has("map_session") || ["options", "recipe"].includes(resumeView))) {
    try {
      const data = await window.RecipeExplorer.restore(token);
      recipeEntry = "fridge"; state.options = data.recipes;
      state.detection = restoredInventory(data);
      renderItems(); renderOptions(data.recipes); show("options");
      const selected = params.get("recipe_id") || sessionStorage.getItem("cookquest.map.selected");
      if (selected && (resumeView === "recipe" || params.has("recipe_id"))) await window.RecipeExplorer.select(Number(selected));
    } catch (error) { showError(error.message, false, "home"); }
  } else if (currentScreen === "home" && resumeView === "review") {
    try { const saved = JSON.parse(sessionStorage.getItem('cookquest.inventory')); if (Array.isArray(saved?.detected_items)) { state.detection = saved; renderReview({manual:true, message:'Your ingredients are saved. Check them, then explore recipes.'}); show('review'); } } catch {}
  }
})();

function renderCatalog() {
  renderOptions(catalog, "catalog-list");
  $("catalog-status").textContent = catalog.length ? `${catalog.length} source-backed recipes · only your confirmed ingredients · axes are estimates` : NO_MATCH_TEXT;
}
async function findRecipes() {
  if (recipeInFlight) return;
  const savedMap = localStorage.getItem("cookquest.map.session");
  if (savedMap) {
    recipeEntry = "fridge";
    try {
      const data = await window.RecipeExplorer.restore(savedMap);
      state.detection = restoredInventory(data); renderItems();
      state.options = data.recipes; renderOptions(data.recipes); show("options"); return;
    } catch { localStorage.removeItem("cookquest.map.session"); }
  }
  const history = localStorage.getItem("cookquest.discovery.history");
  if (!history) { show("scan"); return; }
  recipeEntry = "find"; show("find");
  if (!catalog.length) {
    $("catalog-status").textContent = "Loading the collection…";
    recipeInFlight = true;
    loading('Discovering your culinary world…');
    try {
      const data = await window.RecipeExplorer.discover({ingredients: items().map(i => i.name), pantry_staples: []});
      catalog = data.recipes;
    } catch (error) { showError(error.message, false, 'find', findRecipes); return; }
    finally { recipeInFlight = false; stopLoading(); }
  }
  renderCatalog();
  show('find');
}
$("btn-home").onclick = () => { show("home"); };
if ($("btn-find-recipes")) $("btn-find-recipes").onclick = findRecipes;
$("btn-scan-fridge").onclick = () => { recipeEntry = "fridge"; show("scan"); };
if ($("btn-restaurant")) $("btn-restaurant").onclick = () => show("restaurant");
$("btn-restaurant-recipes").onclick = findRecipes;
