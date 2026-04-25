// Shared auth helpers used by every signed-in page. Loaded after config.js
// so window.BACKEND_URL is available. Pages get a single global, SpecAuth,
// with the boilerplate that used to be copy-pasted into each page.
(function (global) {
  // Use ?? (not ||) — config.js sets BACKEND_URL = '' in prod for same-origin
  // fetches; empty string is falsy so || would defeat that and route to localhost.
  const API     = window.BACKEND_URL ?? 'http://localhost:8000';
  const TOKEN_K = "spec_token";

  // Capture ?token= from the OAuth redirect once on script load and rewrite the
  // URL to remove it. Prevents the token from leaking into the referer header
  // or browser history.
  (function captureToken() {
    const p = new URLSearchParams(location.search);
    const t = p.get("token");
    if (t) {
      localStorage.setItem(TOKEN_K, t);
      p.delete("token");
      const qs = p.toString();
      history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
    }
  })();

  function token() { return localStorage.getItem(TOKEN_K) || ""; }
  function authHdr() { return { Authorization: "Bearer " + token() }; }

  function redirectToLogin() {
    location.href = API + "/auth/google?next=" + encodeURIComponent(location.pathname);
  }

  function signOut() {
    localStorage.removeItem(TOKEN_K);
    location.reload();
  }

  // Wire the standard sign-out button if present. Default id="signout".
  function wireSignout(id) {
    const el = document.getElementById(id || "signout");
    if (el) el.onclick = signOut;
  }

  function _resolveEl(elOrId) {
    if (!elOrId) return null;
    return typeof elOrId === "string" ? document.getElementById(elOrId) : elOrId;
  }

  // Set a status-banner element to a class + message. cls is "ok" / "err" / "warn"
  // (or any class your CSS supports). Pass "" to clear+hide.
  function banner(elOrId, cls, msg) {
    const el = _resolveEl(elOrId);
    if (!el) return;
    if (!cls && !msg) {
      el.className = "banner hidden";
      el.textContent = "";
      return;
    }
    el.className = "banner " + cls;
    el.textContent = msg;
    el.classList.remove("hidden");
  }

  // Verify the JWT and return the user, normalising legacy "owner" -> "freddy".
  // On 401: clears the token and redirects to login.
  // On other failure: shows a banner (if `bannerEl` provided) and throws.
  async function verify(bannerEl) {
    if (!token()) { redirectToLogin(); throw new Error("no_token"); }
    let res;
    try { res = await fetch(API + "/auth/verify?token=" + encodeURIComponent(token())); }
    catch (e) { banner(bannerEl, "err", "Network error: " + e.message); throw e; }
    if (res.status === 401) {
      localStorage.removeItem(TOKEN_K);
      redirectToLogin();
      throw new Error("unauthorized");
    }
    if (!res.ok) {
      banner(bannerEl, "err", "Auth check failed: " + res.status);
      throw new Error("auth_check_failed");
    }
    const { user } = await res.json();
    if (user.role === "owner") user.role = "freddy";
    return user;
  }

  // Wrapped fetch that adds the auth header and auto-redirects on 401. Returns
  // the Response unchanged otherwise. Use it for any backend API call from a
  // signed-in page.
  async function fetchAuth(path, opts) {
    const o = opts || {};
    o.headers = Object.assign({}, o.headers || {}, authHdr());
    const res = await fetch(API + path, o);
    if (res.status === 401) {
      localStorage.removeItem(TOKEN_K);
      redirectToLogin();
      throw new Error("unauthorized");
    }
    return res;
  }

  global.SpecAuth = {
    API, TOKEN_K,
    token, authHdr,
    redirectToLogin, signOut, wireSignout,
    banner, verify, fetchAuth,
  };
})(window);
