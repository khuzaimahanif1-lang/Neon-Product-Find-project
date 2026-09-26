const base = window.NEONFIND_CONFIG?.apiBase || (['5173', '4173'].includes(location.port) ? `${location.protocol}//${location.hostname}:8001/api` : '/api');
const API_BASE = new URL(base, location.origin).toString().replace(/\/$/, '');
export const catalogInfo = { mode: 'live', partial: false, providers: [], currency: 'PKR', exchangeRates: {} };

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      credentials: 'include', signal: AbortSignal.timeout(15000), ...options,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers },
    });
  } catch { throw new Error('The backend is unavailable. Start FastAPI on port 8001 and try again.'); }
  const data = await response.json();
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? data.detail.map(item => item.msg.replace('Value error, ', '')).join(' ') : data.detail;
    const error = new Error(detail || 'The request could not be completed.');
    error.status = response.status;
    throw error;
  }
  return data;
}
export async function loadMetadata() { const data = await request('/catalog/meta'); Object.assign(catalogInfo, data); return data; }
export async function searchProducts(query = '', filters = {}) {
  const params = new URLSearchParams({ q: query });
  for (const [key, value] of Object.entries(filters)) {
    if (Array.isArray(value)) value.forEach(item => params.append(key, item));
    else if (value !== null && value !== undefined) params.set(key, value);
  }
  const data = await request(`/products?${params}`);
  Object.assign(catalogInfo, { mode: data.mode, partial: data.partial, providers: data.providers });
  return data.products;
}
export function streamSearch(query, filters, onEvent, signal) {
  return new Promise((resolve, reject) => {
    const url = new URL(`${API_BASE}/ws/search`);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(url);
    const requestId = crypto.randomUUID();
    let finished = false;
    const timer = setTimeout(() => finish(new Error('The live search timed out. Try again.')), 40000);
    const abort = () => finish(new DOMException('Search cancelled', 'AbortError'));
    function finish(error, data) {
      if (finished) return;
      finished = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      socket.close();
      if (error) reject(error); else resolve(data);
    }
    if (signal?.aborted) { abort(); return; }
    signal?.addEventListener('abort', abort, { once: true });
    socket.onopen = () => socket.send(JSON.stringify({ requestId, q: query, ...filters }));
    socket.onmessage = event => {
      try {
        const data = JSON.parse(event.data);
        if (data.requestId !== requestId || finished) return;
        if (data.type === 'error') { finish(new Error(data.message)); return; }
        Object.assign(catalogInfo, { mode: data.mode, partial: data.partial, providers: data.providers, exchangeRates: data.exchangeRates });
        onEvent(data);
        if (data.type === 'complete') finish(null, data);
      } catch (error) { finish(error); }
    };
    socket.onerror = () => finish(new Error('Could not connect to live search. Start the FastAPI backend on port 8001.'));
    socket.onclose = () => { if (!finished) finish(new Error('The live connection closed. Try your search again.')); };
  });
}
export async function authenticate({ name, email, password, mode }) {
  const data = await request(`/auth/${mode === 'signup' ? 'signup' : 'login'}`, { method: 'POST', body: JSON.stringify(mode === 'signup' ? { name, email, password } : { email, password }) });
  return data.user;
}
export async function loadCurrentUser() { try { return await request('/auth/me'); } catch (error) { if (error.status === 401) return null; throw error; } }
export async function logout() { await request('/auth/logout', { method: 'POST' }); }
export async function getProductOffers(id) { return (await request(`/products/${encodeURIComponent(id)}/offers`)).offers; }
export async function loadProduct(id) { return request(`/products/${encodeURIComponent(id)}`); }
export async function compareProducts(ids) { return (await request('/products/compare', { method: 'POST', body: JSON.stringify({ ids }) })).products; }
export async function loadSavedProducts() { return (await request('/me/saved')).products; }
export async function setSaved(id, saved) { await request(`/me/saved/${encodeURIComponent(id)}`, { method: saved ? 'PUT' : 'DELETE' }); }
export async function loadRecentSearches() { return (await request('/me/searches')).queries; }
export async function recordSearch(query) { await request('/me/searches', { method: 'POST', body: JSON.stringify({ query }) }); }
export async function clearRecentSearches() { await request('/me/searches', { method: 'DELETE' }); }
