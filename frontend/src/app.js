import { stores, categories } from './data.js';
import { streamSearch, authenticate, getProductOffers, loadProduct, catalogInfo, loadMetadata, loadCurrentUser, logout, loadSavedProducts, setSaved, loadRecentSearches, recordSearch, clearRecentSearches, compareProducts } from './api.js';
import { icon } from './icons.js';
import { artwork } from './artwork.js';

const app = document.querySelector('#app');
const modalRoot = document.querySelector('#modal-root');
const money = (value, currency = 'PKR') => value === null || value === undefined ? 'Not published' : new Intl.NumberFormat('en-PK', { style: 'currency', currency, maximumFractionDigits: 2 }).format(value);
const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const readList = key => { try { const value = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(value) ? value.filter(item => typeof item === 'string') : []; } catch { return []; } };
const persist = (key, value) => { try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* The UI also works without browser storage. */ } };
const validRoutes = ['login', 'signup', 'discover', 'recommendations', 'saved', 'recent'];
const currentRoute = () => validRoutes.includes(location.hash.slice(1)) ? location.hash.slice(1) : 'login';
const state = { route: currentRoute(), user: null, query: '', results: [], category: 'all', selectedStores: [], maxPrice: null, freeShipping: false, minRating: 4, minSales: 100, sort: 'best-selling', saved: readList('neonfind-saved'), recent: readList('neonfind-recent').slice(0, 8), compared: [], loading: false, searchError: '', searched: false, providers: [], mobileMenu: false };
let toastTimer;
let searchRequest = 0;
let searchController;
let identityRevision = 0;
let lastDialogFocus;

const storeById = id => stores.find(store => store.id === id);
const productCatalog = new Map();
const offerStoreName = offer => offer.storeName || storeById(offer.store)?.name || offer.store;
const storeMark = id => { const store = storeById(id); return store ? `<span class="store-mark ${store.id}" title="${store.name}">${store.mark}</span>` : ''; };
const logo = () => `<a class="brand" href="#login" aria-label="NeonFind home"><span class="brand-mark">n<span></span></span><span>neonfind<span class="brand-dot">.</span></span></a>`;
const isSignedIn = () => Boolean(state.user && !state.user.demo);
const isAuthRoute = () => state.route === 'login' || state.route === 'signup';
const catalogLabel = () => catalogInfo.mode === 'live' ? 'LIVE SEARCH' : 'SAMPLE MODE';
const catalogDisclaimer = () => catalogInfo.mode === 'live' ? 'Marketplace listings are fetched by the backend. Confirm price, variant, stock and delivery at checkout.' : 'Product prices, ratings & offers are sample data.';
async function refreshAccount(user) {
  const revision = identityRevision;
  ++searchRequest;
  searchController?.abort();
  state.results = []; state.query = ''; state.searched = false; state.providers = []; state.loading = false; state.searchError = '';
  state.user = user;
  state.compared = [];
  state.saved = [];
  state.recent = [];
  if (isSignedIn()) {
    const [saved, recent] = await Promise.all([loadSavedProducts(), loadRecentSearches()]);
    if (revision !== identityRevision) return;
    for (const product of saved) productCatalog.set(product.id, product);
    state.saved = saved.map(product => product.id);
    state.recent = recent;
  } else {
    state.saved = readList('neonfind-saved');
    state.recent = readList('neonfind-recent').slice(0, 8);
    const remembered = await Promise.allSettled(state.saved.slice(0, 40).map(loadProduct));
    if (revision !== identityRevision) return;
    for (const result of remembered) if (result.status === 'fulfilled') productCatalog.set(result.value.id, result.value);
  }
}
async function loadCatalog() {
  if (state.query) return performSearch(state.query, false);
  state.results = []; state.searched = false; state.loading = false;
  if (!isAuthRoute()) render();
}
async function initialize() {
  try {
    const user = await loadCurrentUser();
    if (identityRevision !== 0) return;
    await refreshAccount(user);
  } catch { /* The next search or login shows an actionable connection error. */ }
  try { await loadMetadata(); } catch { /* Search reports backend availability. */ }
  if (identityRevision === 0 && !isAuthRoute()) render();
}
function toast(message) {
  const el = document.querySelector('#toast');
  el.innerHTML = `${icon('check')}<span>${escape(message)}</span>`;
  el.classList.add('visible');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('visible'), 3800);
}
function navigate(route) {
  state.mobileMenu = false;
  if (location.hash === `#${route}`) { state.route = route; render(); }
  else location.hash = route;
  window.scrollTo({ top: 0, behavior: 'instant' });
}
function authScreen() {
  const signup = state.route === 'signup';
  return `<main class="auth-layout">
    <section class="auth-story">
      ${logo()}<div class="auth-story-content"><span class="eyebrow"><span class="live-dot"></span> A BETTER WAY TO FIND YOUR FAVORITES</span><h1>Your next<br>best thing.<br><span>Found.</span></h1><p>One search. More stores. Better choices.<br>Meet your new shopping superpower.</p>
      <div class="auth-product"><div class="orbital orbital-one"></div><div class="orbital orbital-two"></div>${artwork('headphones', 'auth')}<div class="floating-match">${icon('sparkles')}<span><strong>Made for your wishlist</strong><small>Find a little more. Pay a little less.</small></span></div><span class="floating-price">${icon('tag')} Best price, found.</span></div>
      <div class="auth-proof"><span class="proof-icon">${icon('globe')}</span><span><strong>All your favorite stores, together.</strong><small>Daraz · Amazon · Alibaba · and more</small></span></div></div><div class="auth-story-footer"><span>Find better. Feel better.</span><span>✦</span><span>THE SMARTER SEARCH</span></div>
    </section>
    <section class="auth-form-section"><div class="auth-top"><span>${signup ? 'Already have an account?' : 'First time here?'}</span><button class="text-button" data-action="route" data-route="${signup ? 'login' : 'signup'}">${signup ? 'Sign in' : 'Create an account'} ${icon('arrow')}</button></div>
      <div class="auth-form-wrap"><div class="auth-symbol">${icon(signup ? 'sparkles' : 'bag')}</div><span class="eyebrow muted">YOUR NEXT FIND STARTS HERE</span><h2>${signup ? 'Let’s find your favorites.' : 'Good to see you again.'}</h2><p class="auth-description">${signup ? 'Create your space for smarter shopping.' : 'Sign in to discover a little more possibility.'}</p>
      <div class="auth-tabs"><button class="${!signup ? 'active' : ''}" data-action="route" data-route="login">Log in</button><button class="${signup ? 'active' : ''}" data-action="route" data-route="signup">Sign up</button></div>
      <form id="auth-form" novalidate>${signup ? `<label class="field-label" for="full-name">Your name</label><div class="input-wrap">${icon('user')}<input id="full-name" name="name" placeholder="Alex Morgan" autocomplete="name" minlength="2" maxlength="60" required></div>` : ''}
        <label class="field-label" for="email">Email address</label><div class="input-wrap">${icon('mail')}<input type="email" id="email" name="email" placeholder="you@example.com" autocomplete="email" maxlength="254" required></div>
        <div class="password-label"><label class="field-label" for="password">Password</label>${!signup ? '<button type="button" class="text-button small" data-action="forgot">Forgot password?</button>' : ''}</div><div class="input-wrap">${icon('lock')}<input type="password" id="password" name="password" placeholder="${signup ? 'At least 8 characters' : 'Enter your password'}" autocomplete="${signup ? 'new-password' : 'current-password'}" minlength="${signup ? 8 : 1}" maxlength="128"${signup ? ' aria-describedby="password-hint"' : ''} required><button type="button" class="icon-button password-toggle" data-action="password" aria-label="Show password" aria-pressed="false">${icon('eye')}</button></div>
        ${signup ? '<p class="auth-password-hint" id="password-hint">Use 8–128 characters for your password.</p>' : ''}
        <p class="form-error" id="auth-error" role="alert"></p>
        <button type="submit" class="primary-button auth-submit">${signup ? 'Create account' : 'Log in'} ${icon('arrow')}</button>
      </form><div class="auth-divider"><span></span><small>or take a look around</small><span></span></div><button class="secondary-button guest-button" data-action="guest">Browse as guest ${icon('arrow')}</button>
      <p class="demo-note">${icon('shield')} Real accounts · Passwords are securely hashed.</p>
      </div><footer class="auth-footer"><span>© ${new Date().getFullYear()} NeonFind</span><button class="text-button small" data-action="help">Need a hand? ${icon('help')}</button></footer>
    </section>
  </main>`;
}

function sidebar() {
  const links = [{ route: 'discover', name: 'Discover', glyph: 'grid' }, { route: 'recommendations', name: 'For you', glyph: 'sparkles', tag: 'SMART' }, { route: 'saved', name: 'Saved finds', glyph: 'heart', count: state.saved.length }, { route: 'recent', name: 'Recent searches', glyph: 'history' }];
  return `<aside class="sidebar ${state.mobileMenu ? 'is-open' : ''}">${logo()}<div class="workspace-label">YOUR SHOPPING SPACE</div><nav aria-label="Main navigation">${links.map(link => `<button class="nav-link ${state.route === link.route ? 'active' : ''}" data-action="route" data-route="${link.route}" ${state.route === link.route ? 'aria-current="page"' : ''}>${icon(link.glyph)}<span>${link.name}</span>${link.tag ? `<span class="nav-tag">${link.tag}</span>` : link.count ? `<span class="nav-count">${link.count}</span>` : ''}</button>`).join('')}<button class="nav-link" data-action="compare">${icon('compare')}<span>Compare products</span>${state.compared.length ? `<span class="nav-count">${state.compared.length}</span>` : ''}</button></nav>
    <div class="sidebar-bottom"><div class="smart-card"><span class="smart-card-icon">${icon('bolt')}</span><h3>Good finds.<br>Great decisions.</h3><p>See the bigger picture.<br>Find a better deal.</p><button data-action="route" data-route="recommendations">Explore your picks ${icon('arrow')}</button></div><button class="nav-link help-link" data-action="help">${icon('help')}<span>Help & information</span>${icon('external')}</button><div class="sidebar-footer"><span class="live-dot"></span> A LITTLE SMARTER, EVERY DAY</div></div></aside>`;
}

function hero() {
  return `<section class="hero"><div class="hero-copy"><span class="eyebrow"><span class="live-dot"></span> GOOD FINDS START WITH ONE SEARCH</span><h1>Less searching.<br>More <em>finding.</em></h1><p>Your favorite stores. One simple search.<br>Compare the options and find the one that feels right.</p><div class="hero-benefits"><span>${icon('globe')} Multiple stores</span><span>${icon('tag')} Better prices</span><span>${icon('sparkles')} Smarter picks</span></div></div>
    <div class="hero-art"><div class="hero-orbit"></div><div class="hero-orbit inner"></div><span class="hero-cross one">+</span><span class="hero-cross two">+</span>${artwork('headphones', 'hero')}<span class="hero-label">YOUR NEXT GREAT FIND</span><div class="hero-match">${icon('sparkles')} <span>Looks like a <strong>perfect match.</strong></span><span class="tiny-dot"></span></div><span class="art-caption">CURATED FOR THE WAY YOU SHOP</span></div></section>
    <section class="search-section" aria-label="Search products"><form class="search-bar" id="search-form">${icon('search')}<input id="product-search" name="query" value="${escape(state.query)}" placeholder="What’s on your wishlist? Search any product…" aria-label="Search any product" maxlength="120"><kbd>/</kbd><button type="submit" class="primary-button">Find my product ${icon('arrow')}</button></form><div class="trending-searches"><span>Try something:</span>${['Headphones', 'iPhone', 'Smartwatch', 'Laptop'].map(query => `<button data-action="search" data-query="${query}">${query} ${icon('chevron')}</button>`).join('')}<span class="search-note">${icon('shield')} ${catalogInfo.mode === 'live' ? 'Live search · Ranked by the backend' : 'Sample catalog · Real backend'}</span></div></section>`;
}

function retailerStrip() {
  return `<section class="retailer-strip" aria-label="Marketplace sources"><span>MORE STORES.<br><strong>MORE POSSIBILITIES.</strong></span><div class="retailer-logos">${stores.map(store => `<button class="retailer-logo ${state.selectedStores.includes(store.id) ? 'selected' : ''}" data-action="store-chip" data-store="${store.id}" aria-pressed="${state.selectedStores.includes(store.id)}">${storeMark(store.id)}<span>${store.name}</span></button>`).join('')}</div><span class="retailer-end">All in one place ${icon('arrow')}</span></section>`;
}

function visibleProducts() {
  return state.route === 'saved' ? state.saved.map(id => productCatalog.get(id)).filter(Boolean) : state.results;
}
function productVisual(product, suffix = '') {
  return product.imageUrl ? `<img class="marketplace-image" src="${escape(product.imageUrl)}" alt="${escape(product.name)}" loading="lazy" referrerpolicy="no-referrer">` : artwork(product.art, `${product.id}${suffix}`);
}
function published(value) { return value === null || value === undefined ? 'Not published' : Number(value).toLocaleString(); }
function searchFilters() { return { store: state.selectedStores.length ? state.selectedStores : null, category: state.category === 'all' ? null : state.category, maxPrice: state.maxPrice, freeShipping: state.freeShipping, minRating: state.minRating, minSales: state.minSales, sort: state.sort }; }
async function applyFilters() { if (state.query && state.route !== 'saved') await performSearch(state.query, false); else render(); }
function providerPanel() {
  if (!state.searched || !state.providers.length) return '';
  return `<section class="provider-panel" aria-label="Live marketplace progress" aria-live="polite">${state.providers.map(provider => `<div class="provider-state ${provider.status}">${storeMark(provider.store)}<div><strong>${escape(provider.name)}</strong><span>${provider.status === 'searching' ? 'Searching…' : provider.status === 'ok' ? `${provider.count} listings${provider.cached ? ' · cached' : ''}` : provider.status === 'demo' ? 'Sample data' : escape(provider.message || 'Unavailable')}${provider.notice ? `<small>${escape(provider.notice)}</small>` : ''}</span></div><span class="provider-indicator">${provider.status === 'searching' ? '<span class="spinner"></span>' : icon(provider.status === 'ok' ? 'check' : provider.status === 'error' ? 'help' : 'globe')}</span></div>`).join('')}${catalogInfo.mode === 'live' && !catalogInfo.shopifyConfigured ? '<p class="source-configuration-hint">Shopify: add the stores you want to search in backend settings. Public storefronts usually do not expose sales totals.</p>' : ''}</section>`;
}
function filters() {
  return `<aside class="filters" aria-label="Product filters"><div class="filter-title"><h3>Fine-tune your finds</h3><button class="text-button small" data-action="reset">Reset</button></div><div class="filter-group"><h4>Stores <span>${stores.length}</span></h4>${stores.map(store => `<label class="checkbox-row"><input type="checkbox" name="store" value="${store.id}" ${state.selectedStores.includes(store.id) ? 'checked' : ''}>${storeMark(store.id)}<span>${store.name}</span><small>${state.results.filter(product => product.offers.some(offer => offer.store === store.id)).length}</small></label>`).join('')}</div>
    <div class="filter-group quality-filters"><h4>Sales &amp; rating</h4><label for="min-rating">Minimum rating</label><select id="min-rating" aria-label="Minimum rating">${[[0,'Any rating'],[4,'4+ stars'],[4.5,'4.5+ stars'],[4.8,'4.8+ stars']].map(([value,label]) => `<option value="${value}" ${(state.minRating || 0) === value ? 'selected' : ''}>${label}</option>`).join('')}</select><label for="min-sales">Minimum published sales</label><select id="min-sales" aria-label="Minimum published sales">${[[0,'Any sales / unknown'],[100,'100+ sales'],[500,'500+ sales'],[1000,'1,000+ sales'],[10000,'10,000+ sales']].map(([value,label]) => `<option value="${value}" ${(state.minSales || 0) === value ? 'selected' : ''}>${label}</option>`).join('')}</select><p class="filter-hint">Missing metrics are excluded when a minimum is set. Sales periods differ by store.</p></div><div class="filter-group"><h4>Price range</h4><div class="price-endpoints"><span>Rs. 0</span><output id="price-output" for="max-price">${state.maxPrice === null ? 'Any budget' : money(state.maxPrice)}</output></div><input class="price-slider" id="max-price" type="range" min="0" max="320000" step="1000" value="${state.maxPrice ?? 320000}" aria-label="Maximum price"><p class="filter-hint">Budget in PKR. Converted amounts are estimates.</p></div>
    <div class="filter-group"><h4>The little extras</h4><label class="checkbox-row"><input type="checkbox" name="free-shipping" ${state.freeShipping ? 'checked' : ''}><span>Free delivery</span>${icon('truck')}</label></div><div class="filter-tip">${icon('sparkles')}<p>We bring the options together.<br><strong>You make the great decision.</strong></p></div></aside>`;
}
function productCard(product) {
  const saved = state.saved.includes(product.id), compared = state.compared.includes(product.id);
  const best = product.offers[0];
  const discount = product.oldPrice > product.price ? Math.round((1 - product.price / product.oldPrice) * 100) : 0;
  return `<article class="product-card"><div class="product-image"><span class="product-badge best">${escape(product.source === 'demo' ? 'Sample saved record' : product.badge)}</span><button class="save-button ${saved ? 'saved' : ''}" data-action="save" data-id="${product.id}" aria-label="${saved ? 'Unsave' : 'Save'} ${escape(product.name)}" aria-pressed="${saved}">${icon('heart')}</button><button class="art-button" data-action="details" data-id="${product.id}" aria-label="View ${escape(product.name)} offers">${productVisual(product)}</button>${discount ? `<span class="discount-badge">−${discount}%</span>` : ''}</div>
    <div class="product-body"><div class="product-meta"><span>${escape(product.brand.toUpperCase())}</span><span class="rating">${icon('star')} ${product.rating === null ? 'Unrated' : Number(product.rating).toFixed(1)} <small>(${published(product.reviews)})</small></span></div><button class="product-name" data-action="details" data-id="${product.id}">${escape(product.name)}</button><p class="product-subtitle">${escape(product.subtitle || 'Open to inspect listing information.')}</p><p class="product-sales">${escape(product.salesLabel || 'Sales count not published')}</p><div class="product-price"><strong>${money(product.price, product.currency)}</strong>${discount ? `<del>${money(product.oldPrice, product.currency)}</del>` : ''}</div>${product.currency !== product.comparisonCurrency && product.comparisonPrice !== null ? `<p class="converted-price">≈ ${money(product.comparisonPrice, product.comparisonCurrency)}</p>` : ''}<p class="price-basis">${escape(product.priceBasis)}</p><div class="product-store"><span>${storeMark(best.store)} <strong>${escape(offerStoreName(best))}</strong></span><span class="available-stores">${product.offers.length} offer${product.offers.length === 1 ? '' : 's'}</span></div><div class="product-actions">${best.source === 'live' && best.url ? `<a class="deal-button" href="${escape(best.url)}" target="_blank" rel="noopener noreferrer">Buy on ${escape(offerStoreName(best))} ${icon('external')}</a>` : `<button class="deal-button" data-action="details" data-id="${product.id}">Inspect offers ${icon('arrow')}</button>`}<button class="compare-icon ${compared ? 'selected' : ''}" data-action="toggle-compare" data-id="${product.id}" aria-label="${compared ? 'Remove' : 'Add'} ${escape(product.name)} ${compared ? 'from' : 'to'} comparison" aria-pressed="${compared}">${icon(compared ? 'check' : 'compare')}</button></div></div></article>`;
}
function spotlight(product) {
  return `<section class="spotlight"><div class="spotlight-icon">${icon('sparkles')}</div><div class="spotlight-copy"><span class="eyebrow">A LITTLE HELP CHOOSING</span><h3>Your ${state.query ? 'search' : 'next'} favorite might be right here.</h3><p><strong>${escape(product.name)}</strong> leads these results with a ${product.score}% ranking score.</p></div><button class="spotlight-score" data-action="score" aria-label="About ranking scores"><strong>${product.score}<small>%</small></strong><span>RANKING SCORE</span></button><button class="spotlight-button" data-action="details" data-id="${product.id}">Meet your top pick ${icon('arrow')}</button></section>`;
}

function catalog() {
  const list = visibleProducts(), savedView = state.route === 'saved';
  if (!savedView && !state.searched) return `<section class="catalog search-intro"><div class="empty-state">${icon('search')}<span class="eyebrow muted">YOUR SEARCH STARTS HERE</span><h3>What are you looking for?</h3><p>Search above to fetch marketplace listings, compare offers, and see the strongest matches. Products appear after you search.</p></div></section>`;
  const title = savedView ? 'Your saved finds' : `Results for “${escape(state.query)}”`;
  return `<section class="catalog">${!savedView ? providerPanel() : ''}<div class="section-heading"><div><span class="eyebrow muted">${savedView ? 'GOOD THINGS, KEPT CLOSE' : 'ONE SEARCH. ALL THE OPTIONS.'}</span><h2>${title}<span class="heading-dot">.</span></h2><p>${savedView ? 'Keep your favorites together.' : 'Sales and ratings are filtered and ranked on the backend. Adjust minimums to include more listings.'}</p></div><div class="catalog-count"><span class="live-dot"></span>${list.length} finds${state.loading ? ' · searching' : ''}</div></div>
    ${!savedView ? `<div class="catalog-toolbar"><div class="category-tabs" role="group" aria-label="Product categories">${categories.map(category => `<button class="category-tab ${state.category === category.id ? 'active' : ''}" data-action="category" data-category="${category.id}" aria-pressed="${state.category === category.id}">${icon(category.icon)}${category.name}</button>`).join('')}</div><label class="sort-label">Sort by <select id="sort" aria-label="Sort products">${[['best-selling','Sales & rating'],['recommended','Recommended'],['price-low','Price: low to high'],['price-high','Price: high to low'],['rating','Highest rated'],['sales','Published sales']].map(([value,label]) => `<option value="${value}" ${state.sort === value ? 'selected' : ''}>${label}</option>`).join('')}</select></label></div>` : ''}
    <div class="catalog-layout ${savedView ? 'saved-layout' : ''}">${!savedView ? filters() : ''}<div class="catalog-results">${state.searchError ? `<div class="provider-warning" role="alert">${escape(state.searchError)} <button class="text-button" data-action="search" data-query="${escape(state.query)}">Try again</button></div>` : ''}${state.loading ? '<div class="stream-loading" role="status"><span class="spinner"></span> Comparing marketplace responses as they arrive…</div>' : ''}${list.length ? `<div class="product-grid">${list.map(productCard).join('')}</div>${!savedView && !state.loading ? spotlight(list[0]) : ''}` : !state.loading ? `<div class="empty-state">${icon(savedView ? 'heart' : 'search')}<h3>${savedView ? 'A little space for your favorites.' : 'No matching products returned.'}</h3><p>${savedView ? 'Tap the heart on a product after searching.' : 'Lower the rating or sales minimums, try another product, or check the source statuses. Unknown metrics do not meet active minimums.'}</p><button class="primary-button" data-action="route" data-route="discover">Search products ${icon('arrow')}</button></div>` : ''}</div></div></section>`;
}
function recentScreen() {
  return `<section class="recent-section"><span class="eyebrow muted">PICK UP WHERE YOU LEFT OFF</span><h2>Your recent searches<span class="heading-dot">.</span></h2><p>A good find is always worth another look.</p>${state.recent.length ? `<div class="recent-list">${state.recent.map(query => `<button data-action="search" data-query="${escape(query)}">${icon('history')}<span>${escape(query)}</span><small>Search again</small>${icon('arrow')}</button>`).join('')}</div><button class="text-button" data-action="clear-recent">Clear recent searches ${icon('close')}</button>` : `<div class="empty-state">${icon('history')}<h3>Your next search starts a story.</h3><p>Search for a product and you’ll find it here.</p><button class="primary-button" data-action="route" data-route="discover">Start discovering ${icon('arrow')}</button></div>`}</section>`;
}
function dashboard() {
  const title = { discover: 'Discover', recommendations: 'For you', saved: 'Saved finds', recent: 'Recent searches' }[state.route];
  const name = state.user?.name || 'Explorer';
  return `<div class="dashboard">${sidebar()}${state.mobileMenu ? '<button class="sidebar-scrim" data-action="menu" aria-label="Close navigation"></button>' : ''}<div class="main-shell"><header class="topbar"><div class="breadcrumb"><button class="icon-button mobile-menu" data-action="menu" aria-label="Open navigation" aria-expanded="${state.mobileMenu}">${icon('menu')}</button>${icon('grid')}<span>Your shopping space</span>${icon('chevron')}<strong>${title}</strong></div><div class="topbar-actions"><span class="demo-pill"><span class="tiny-dot"></span> ${catalogLabel()}</span><button class="icon-button notification-button" data-action="notifications" aria-label="Notifications">${icon('bell')}<span></span></button><span class="topbar-separator"></span><button class="profile-button" data-action="profile" aria-label="View profile"><span class="avatar">${escape(name.slice(0, 1).toUpperCase())}</span><span>${escape(name.split(' ')[0])}</span>${icon('down')}</button></div></header><main class="main-content">${state.route === 'discover' || state.route === 'recommendations' ? `${hero()}${retailerStrip()}` : ''}${state.route === 'recent' ? recentScreen() : catalog()}<footer class="page-footer"><span>Good finds are just the beginning. <span class="footer-star">✦</span></span><span>NEONFIND · BUILT FOR BETTER CHOICES</span><span>${catalogDisclaimer()}${catalogInfo.exchangeRates?.source === 'ExchangeRate-API' ? ' <a href="https://www.exchangerate-api.com" target="_blank" rel="noopener noreferrer">Rates by ExchangeRate-API</a>' : ''}</span></footer></main></div>${state.compared.length ? `<div class="comparison-tray"><div class="tray-label">${icon('compare')}<strong>${state.compared.length} ${state.compared.length === 1 ? 'product' : 'products'} selected</strong><span>Find your favorite, side by side.</span></div><button class="text-button small" data-action="clear-compare">Clear</button><button class="primary-button" data-action="compare">Compare now ${icon('arrow')}</button></div>` : ''}</div>`;
}
function render() {
  const auth = state.route === 'login' || state.route === 'signup';
  document.title = `${auth ? state.route === 'signup' ? 'Sign up' : 'Log in' : 'Discover your next find'} — NeonFind`;
  const input = document.activeElement?.id === 'product-search' ? document.activeElement : null;
  const draft = input ? { value: input.value, start: input.selectionStart, end: input.selectionEnd } : null;
  app.innerHTML = auth ? authScreen() : dashboard();
  if (draft) { const next = document.querySelector('#product-search'); if (next) { next.value = draft.value; next.focus(); next.setSelectionRange(draft.start, draft.end); } }
}

function openDialog(title, body, className = '') {
  lastDialogFocus = document.activeElement;
  modalRoot.innerHTML = `<dialog class="dialog ${className}" aria-labelledby="dialog-title"><div class="dialog-header"><div><span class="eyebrow muted">A CLOSER LOOK</span><h2 id="dialog-title">${title}</h2></div><button class="icon-button" data-action="close-dialog" aria-label="Close dialog">${icon('close')}</button></div>${body}</dialog>`;
  const dialog = modalRoot.querySelector('dialog');
  dialog.addEventListener('click', event => { if (event.target === dialog) { const bounds = dialog.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close(); } });
  dialog.addEventListener('close', () => { modalRoot.innerHTML = ''; if (lastDialogFocus?.isConnected) lastDialogFocus.focus(); });
  dialog.showModal();
}
async function showDetails(id) {
  const product = productCatalog.get(id);
  if (!product) return;
  try {
    const offers = await getProductOffers(id);
    openDialog(escape(product.name), `<div class="detail-product"><div class="detail-art">${productVisual(product, '-dialog')}</div><div><span class="product-badge best">${escape(product.source === 'demo' ? 'Sample saved record' : product.badge)}</span><div class="detail-rating">${icon('star')} ${product.rating === null ? 'Rating not published' : Number(product.rating).toFixed(1) + ' / 5'} <span>· ${published(product.reviews)} reviews</span></div><strong class="detail-price">${money(product.price, product.currency)}</strong><p>${escape(product.salesLabel || 'Sales count not published')}</p><button class="text-button" data-action="score">${icon('sparkles')} ${product.score}% ranking score · How it works</button></div></div>${product.overview.length ? `<section class="listing-overview"><h3>Listing overview</h3><ul>${product.overview.map(line => `<li>${escape(line)}</li>`).join('')}</ul></section>` : '<p class="dialog-disclaimer">The marketplace did not publish an overview in its search results. View the original listing for full specifications.</p>'}${Object.keys(product.specifications).length ? `<dl class="listing-specifications">${Object.entries(product.specifications).map(([key,value]) => `<dt>${escape(key)}</dt><dd>${escape(value)}</dd>`).join('')}</dl>` : ''}<div class="offers-heading"><h3>Original marketplace offers</h3><span>${offers.length} offers</span></div><div class="offer-list">${offers.map(offer => `<div class="offer-row"><div class="offer-store">${storeMark(offer.store)}<div><strong>${escape(offerStoreName(offer))}</strong><small>${escape(offer.seller || 'Seller not published')}</small><small>${escape(offer.shipping)} · ${escape(offer.delivery)}</small>${offer.salesLabel ? `<small>${escape(offer.salesLabel)}</small>` : ''}${offer.minimumOrder ? `<small>Minimum order: ${escape(offer.minimumOrder)}</small>` : ''}</div></div><div class="offer-price"><strong>${money(offer.price, offer.currency)}</strong><small>${offer.totalPrice === null ? 'Delivery extra / unknown' : `Total ${money(offer.totalPrice, offer.currency)}`}</small>${offer.priceLabel ? `<small>${escape(offer.priceLabel)}</small>` : ''}</div>${offer.source === 'live' && offer.url ? `<a class="secondary-button small-button" href="${escape(offer.url)}" target="_blank" rel="noopener noreferrer">Buy on ${escape(offerStoreName(offer))} ${icon('external')}</a>` : '<span class="demo-pill">No live purchase link</span>'}${offer.observedAt ? `<small class="offer-observed">Fetched ${escape(new Date(offer.observedAt).toLocaleString())}</small>` : ''}</div>`).join('')}</div><p class="dialog-disclaimer">${icon('shield')} ${product.source === 'demo' ? 'This saved record contains illustrative sample data.' : catalogDisclaimer()} Sales labels retain the store's reporting period. Converted prices are approximate; confirm your final payable amount on the store.</p>`, 'detail-dialog');
  } catch (error) { toast(error.message || 'Could not load offers.'); }
}
async function showComparison() {
  if (!state.compared.length) { toast('Use the compare icon on a product to add up to 4 finds.'); return; }
  const selected = await compareProducts(state.compared);
  const rows = [
    ['Listed price', product => `<strong>${money(product.price, product.currency)}</strong>`],
    ['Published total', product => money(product.offers[0].totalPrice, product.currency)],
    ['Comparison estimate', product => product.comparisonPrice === null ? 'Conversion unavailable' : `≈ ${money(product.comparisonPrice, product.comparisonCurrency)}`],
    ['Price basis', product => escape(product.priceBasis)],
    ['Rating', product => product.rating === null ? 'Not published' : `${Number(product.rating).toFixed(1)} / 5`],
    ['Reviews', product => published(product.reviews)],
    ['Published sales', product => escape(product.salesLabel || 'Not published')],
    ['Ranking score', product => `${product.score}%`],
    ['Seller', product => escape(product.offers[0].seller || 'Not published')],
    ['Delivery', product => escape(product.offers[0].shipping)],
  ];
  openDialog('Find your favorite, side by side.', `<p class="dialog-intro">Different listings can have different variants, quantities and sales reporting periods. Confirm them on the original store.</p><div class="comparison-scroll"><table class="comparison-table"><thead><tr><th scope="col">A little perspective</th>${selected.map(product => `<th scope="col"><div class="comparison-art">${productVisual(product, '-compare')}</div><strong>${escape(product.name)}</strong><button class="text-button small" data-action="remove-compared" data-id="${product.id}">Remove ${icon('close')}</button></th>`).join('')}</tr></thead><tbody>${rows.map(([label, format]) => `<tr><th scope="row">${label}</th>${selected.map(product => `<td>${format(product)}</td>`).join('')}</tr>`).join('')}<tr><th scope="row">Buy from the source</th>${selected.map(product => `<td>${product.offers[0].source === 'live' && product.offers[0].url ? `<a class="deal-button" href="${escape(product.offers[0].url)}" target="_blank" rel="noopener noreferrer">Buy on ${escape(offerStoreName(product.offers[0]))} ${icon('external')}</a>` : 'No purchase link'}</td>`).join('')}</tr><tr><th scope="row">Overview and offers</th>${selected.map(product => `<td><button class="deal-button" data-action="details" data-id="${product.id}">Inspect listing ${icon('arrow')}</button></td>`).join('')}</tr></tbody></table></div>`, 'comparison-dialog');
}

async function performSearch(query, newSearch = true) {
  const request = ++searchRequest;
  searchController?.abort();
  searchController = new AbortController();
  state.query = query.trim().slice(0, 120);
  if (newSearch) state.category = 'all';
  state.results = []; state.providers = []; state.compared = []; state.searchError = '';
  state.searched = Boolean(state.query); state.loading = state.searched;
  navigate('discover'); render();
  if (!state.query) return;
  const revision = identityRevision;
  try {
    await streamSearch(state.query, searchFilters(), event => {
      if (request !== searchRequest || revision !== identityRevision) return;
      state.results = event.products; state.providers = event.providers;
      for (const product of event.products) productCatalog.set(product.id, product);
      state.loading = event.type !== 'complete'; render();
    }, searchController.signal);
    if (request !== searchRequest || revision !== identityRevision) return;
    if (newSearch) {
      if (isSignedIn()) {
        try { await recordSearch(state.query); const recent = await loadRecentSearches(); if (request === searchRequest && revision === identityRevision) state.recent = recent; }
        catch { toast('Results loaded. Your search history could not be saved.'); }
      } else { state.recent = [state.query, ...state.recent.filter(item => item.toLowerCase() !== state.query.toLowerCase())].slice(0, 8); persist('neonfind-recent', state.recent); }
    }
  } catch (error) { if (request !== searchRequest || error.name === 'AbortError') return; state.searchError = error.message; }
  finally { if (request === searchRequest) { state.loading = false; render(); } }
}
function resetFilters() { state.category = 'all'; state.selectedStores = []; state.maxPrice = null; state.freeShipping = false; state.minRating = 4; state.minSales = 100; state.sort = 'best-selling'; }

document.addEventListener('click', async event => {
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const { action, id, route, query, category, store } = button.dataset;
  try {
  if (action === 'route') {
    if (route === 'recommendations') { navigate(route); return; }
    navigate(route);
  } else if (action === 'guest') { ++identityRevision; await logout(); await refreshAccount(null); state.user = { name: 'Explorer', demo: true }; navigate('discover'); await loadCatalog(); }
  else if (action === 'search') await performSearch(query || '');
  else if (action === 'category') { state.category = category; await applyFilters(); }
  else if (action === 'store-chip') { state.selectedStores = state.selectedStores.includes(store) ? state.selectedStores.filter(item => item !== store) : [...state.selectedStores, store]; await applyFilters(); }
  else if (action === 'reset') { resetFilters(); await applyFilters(); }
  else if (action === 'reset-search') { resetFilters(); await performSearch(''); }
  else if (action === 'save') { const revision = identityRevision; const save = !state.saved.includes(id); if (isSignedIn()) await setSaved(id, save); if (revision !== identityRevision) return; state.saved = save ? [...state.saved, id] : state.saved.filter(item => item !== id); if (!isSignedIn()) persist('neonfind-saved', state.saved); render(); toast(save ? 'A good find, saved for later.' : 'Removed from your saved finds.'); }
  else if (action === 'details') await showDetails(id);
  else if (action === 'toggle-compare') { if (state.compared.includes(id)) state.compared = state.compared.filter(item => item !== id); else if (state.compared.length < 4) state.compared.push(id); else { toast('Compare up to 4 products at a time.'); return; } render(); }
  else if (action === 'compare') await showComparison();
  else if (action === 'clear-compare') { state.compared = []; render(); }
  else if (action === 'remove-compared') { state.compared = state.compared.filter(item => item !== id); modalRoot.querySelector('dialog')?.close(); render(); if (state.compared.length) await showComparison(); }
  else if (action === 'clear-recent') { if (isSignedIn()) await clearRecentSearches(); state.recent = []; if (!isSignedIn()) persist('neonfind-recent', []); render(); toast('Recent searches cleared.'); }
  else if (action === 'close-dialog') modalRoot.querySelector('dialog')?.close();
  else if (action === 'password') { const input = document.querySelector('#password'); const visible = input.type === 'password'; input.type = visible ? 'text' : 'password'; button.setAttribute('aria-label', visible ? 'Hide password' : 'Show password'); button.setAttribute('aria-pressed', String(visible)); }
  else if (action === 'menu') { state.mobileMenu = !state.mobileMenu; render(); }
  else if (action === 'sample-offer') toast(`This ${storeById(store)?.name || 'store'} offer is a demo. Connect your API to open real deals.`);
  else if (action === 'notifications') openDialog('You’re all caught up.', `<div class="info-dialog-body">${icon('bell')}<p>Your discoveries start here. Price alerts and notifications can be connected when your backend is ready.</p><span class="demo-pill">DEMO PREVIEW</span></div>`);
  else if (action === 'score') openDialog('A little help choosing.', `<div class="info-dialog-body">${icon('sparkles')}<p>Sales &amp; rating ranking uses search relevance 25%, rating confidence 30%, published sales 30%, review volume 10%, and price value 5%. Recommended mode uses relevance 35%, rating 20%, price value 20%, reviews 10%, sales 10%, and delivery 5%. All filtering and scores are calculated by the backend.</p><p>Fuzzy matching handles common spelling mistakes. Ratings are adjusted for review count. Sales are compared within their source store because reporting periods differ. Published delivery is included; unknown costs remain unknown. This is a weighted ranking, not a probability or a trained AI model. ${catalogDisclaimer()}</p><span class="demo-pill">EXPLAINABLE RANKING</span></div>`);
  else if (action === 'forgot') openDialog('A fresh start for your password.', `<div class="info-dialog-body">${icon('lock')}<p>Email-based password recovery is not configured yet. Log in with the password you registered, create an account, or choose “Browse as guest.”</p><button class="primary-button" data-action="close-dialog">Back to sign in ${icon('arrow')}</button></div>`);
  else if (action === 'profile') openDialog('Your little shopping space.', `<div class="profile-dialog-content"><span class="avatar large">${escape((state.user?.name || 'Explorer').slice(0, 1).toUpperCase())}</span><h3>${escape(state.user?.name || 'Explorer')}</h3><p>${escape(state.user?.email || 'Guest explorer')}</p><span class="demo-pill">${isSignedIn() ? 'SIGNED IN' : 'GUEST SESSION'}</span><p>${state.saved.length} saved finds · ${state.recent.length} recent searches</p><button class="secondary-button" data-action="logout">${icon('logout')} ${isSignedIn() ? 'Log out' : 'Return to login'}</button></div>`);
  else if (action === 'logout') { ++identityRevision; await logout(); modalRoot.querySelector('dialog')?.close(); await refreshAccount(null); navigate('login'); }
  else if (action === 'help') openDialog('Meet your shopping superpower.', `<div class="help-content"><p>NeonFind brings product discovery and store comparison into one place.</p><ol><li>Search a product or explore a category.</li><li>Filter by stores, budget, or free delivery.</li><li>Open a product to inspect live marketplace offers.</li><li>Save a favorite with the heart, or compare up to four products with the sliders icon.</li></ol><p class="dialog-disclaimer">Signup and login use the Python FastAPI backend. Your saved finds and recent searches are stored per account. ${catalogDisclaimer()} Fetching, filtering, caching and ranking run on the backend. Buy links open the original listing. Restricted sources require API access.</p></div>`);
  } catch (error) { toast(error.message || 'Please try again.'); }
});

document.addEventListener('submit', async event => {
  if (event.target.id === 'search-form') { event.preventDefault(); await performSearch(new FormData(event.target).get('query') || ''); }
  if (event.target.id !== 'auth-form') return;
  event.preventDefault();
  const form = event.target;
  const submit = form.querySelector('[type="submit"]');
  const error = form.querySelector('#auth-error');
  const formData = new FormData(form);
  const mode = state.route;
  const name = String(formData.get('name') || '').trim();
  const email = String(formData.get('email') || '').trim();
  const password = String(formData.get('password') || '');
  const invalid = mode === 'signup' && name.length < 2 ? ['name', 'Enter your name using at least 2 characters.']
    : !email || form.elements.email.validity.typeMismatch ? ['email', 'Enter a valid email address, such as you@example.com.']
    : password.length < (mode === 'signup' ? 8 : 1) ? ['password', mode === 'signup' ? 'Your password must contain at least 8 characters.' : 'Enter your password.']
    : password.length > 128 ? ['password', 'Your password must contain no more than 128 characters.']
    : null;
  for (const input of form.querySelectorAll('input')) {
    input.removeAttribute('aria-invalid'); input.removeAttribute('aria-describedby');
    if (mode === 'signup' && input.name === 'password') input.setAttribute('aria-describedby', 'password-hint');
  }
  if (invalid) {
    error.textContent = invalid[1];
    const input = form.elements[invalid[0]];
    input.setAttribute('aria-invalid', 'true'); input.setAttribute('aria-describedby', 'auth-error'); input.focus();
    return;
  }
  if (!form.checkValidity()) { error.textContent = 'Check the highlighted field and try again.'; form.reportValidity(); return; }
  submit.disabled = true;
  submit.innerHTML = '<span class="spinner"></span> ' + (mode === 'signup' ? 'Creating your account…' : 'Signing you in…');
  error.textContent = '';
  ++identityRevision;
  try {
    const user = await authenticate({ name, email, password, mode });
    if (!form.isConnected || state.route !== mode) return;
    form.querySelector('#password').value = '';
    await refreshAccount(user);
    navigate('discover');
    await loadCatalog();
    toast('Welcome to your shopping space.');
  } catch (cause) { if (form.isConnected) { error.textContent = cause.message || 'Something went wrong. Try again.'; submit.disabled = false; submit.innerHTML = `${mode === 'signup' ? 'Create account' : 'Log in'} ${icon('arrow')}`; } }
});
document.addEventListener('change', async event => {
  const input = event.target;
  if (input.name === 'store') { state.selectedStores = [...document.querySelectorAll('input[name="store"]:checked')].map(item => item.value); await applyFilters(); }
  else if (input.id === 'max-price') { state.maxPrice = Number(input.value); await applyFilters(); }
  else if (input.name === 'free-shipping') { state.freeShipping = input.checked; await applyFilters(); }
  else if (input.id === 'min-rating') { state.minRating = Number(input.value) || null; await applyFilters(); }
  else if (input.id === 'min-sales') { state.minSales = Number(input.value) || null; await applyFilters(); }
  else if (input.id === 'sort') { state.sort = input.value; await applyFilters(); }
});
document.addEventListener('input', event => { if (event.target.id === 'max-price') document.querySelector('#price-output').textContent = money(event.target.value); });
document.addEventListener('keydown', event => {
  if (event.key === '/' && !modalRoot.querySelector('dialog') && !event.target.matches('input, textarea, select, [contenteditable]')) { const search = document.querySelector('#product-search'); if (search) { event.preventDefault(); search.focus(); } }
  if (event.key === 'Escape' && state.mobileMenu) { state.mobileMenu = false; render(); }
});
window.addEventListener('hashchange', () => { state.route = currentRoute(); render(); });
render();
initialize();
