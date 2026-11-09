/* Native single-play GIF: compressed frames and original delays stay untouched. */
globalThis.WorldLoading = (() => {
  function inspect(bytes) {
    const text = (a, b) => String.fromCharCode(...bytes.slice(a, b));
    if (!['GIF87a', 'GIF89a'].includes(text(0, 6))) throw new Error('Invalid animation file.');
    let p = 13 + ((bytes[10] & 128) ? 3 * (2 ** ((bytes[10] & 7) + 1)) : 0);
    let start = 0, frames = 0, durationMs = 0, delay = 0;
    const chunks = [];
    const check = () => { if (p > bytes.length) throw new Error('Incomplete animation file.'); };
    function blocks() {
      while (true) { check(); const size = bytes[p++]; if (size === undefined) throw new Error('Incomplete animation file.'); if (!size) break; p += size; }
      check();
    }
    while (p < bytes.length) {
      const offset = p, marker = bytes[p++];
      if (marker === 0x3b) {
        chunks.push(bytes.slice(start, p));
        if (!frames || !durationMs) throw new Error('Animation has no timed frames.');
        return {frames, durationMs, blob: new Blob(chunks, {type: 'image/gif'})};
      }
      if (marker === 0x21) {
        const label = bytes[p++];
        if (label === 0xf9) {
          if (bytes[p] !== 4) throw new Error('Invalid animation timing.');
          delay = (bytes[p + 2] | (bytes[p + 3] << 8)) * 10;
        }
        const looping = label === 0xff && ['NETSCAPE2.0', 'ANIMEXTS1.0'].includes(text(p + 1, p + 12));
        blocks();
        if (looping) { chunks.push(bytes.slice(start, offset)); start = p; }
      } else if (marker === 0x2c) {
        const packed = bytes[p + 8]; p += 9;
        if (packed & 128) p += 3 * (2 ** ((packed & 7) + 1));
        p++; blocks();
        if (!delay) throw new Error('Animation frame is missing its duration.');
        frames++; durationMs += delay; delay = 0;
      } else throw new Error('Unsupported animation block.');
      check();
    }
    throw new Error('Incomplete animation file.');
  }
  let prepared;
  function prepare() {
    if (!prepared) prepared = fetch('/assets/animation.gif', {signal: AbortSignal.timeout(30000)})
      .then(response => { if (!response.ok) throw new Error('Animation could not be loaded. Please retry.'); return response.arrayBuffer(); })
      .then(buffer => inspect(new Uint8Array(buffer))).catch(error => { prepared = null; throw error; });
    return prepared;
  }
  function wait(ms, signal) {
    return new Promise((resolve, reject) => {
      signal?.throwIfAborted();
      const finish = () => { signal?.removeEventListener('abort', abort); resolve(); };
      const timer = setTimeout(finish, ms);
      const abort = () => { clearTimeout(timer); reject(signal.reason); };
      signal?.addEventListener('abort', abort, {once: true});
    });
  }
  async function playOnce(loader, signal) {
    const asset = await prepare(); signal.throwIfAborted();
    const image = document.createElement('img'); image.alt = ''; image.width = 800; image.height = 500;
    loader.querySelector('img').replaceWith(image);
    // Fresh URL prevents sharing an already-running GIF timeline.
    const url = URL.createObjectURL(asset.blob);
    try {
      await new Promise((resolve, reject) => {
        const done = error => { clearTimeout(timer); image.onload = image.onerror = null; signal.removeEventListener('abort', abort); error ? reject(error) : resolve(); };
        const abort = () => done(signal.reason);
        const timer = setTimeout(() => done(new Error('Animation could not be decoded. Please retry.')), 30000);
        image.onload = () => done(); image.onerror = () => done(new Error('Animation could not be decoded. Please retry.'));
        signal.addEventListener('abort', abort, {once: true}); image.src = url;
      });
      await new Promise(resolve => requestAnimationFrame(resolve));
      // Actual sum of original frame delays, not a guessed minimum.
      await wait(asset.durationMs, signal);
    } finally { URL.revokeObjectURL(url); }
  }
  async function waitForBoth(animation, map) {
    const results = await Promise.allSettled([animation, map]);
    for (const result of results) if (result.status === 'rejected') throw result.reason;
    return results[1].value;
  }
  return {inspect, prepare, playOnce, wait, waitForBoth};
})();
