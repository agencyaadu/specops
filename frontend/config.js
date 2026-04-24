// Deployment config. Edit this file and redeploy the frontend
// to point at a different backend. No build step needed.
(function () {
  var isLocal = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  // In prod we proxy /api, /auth, /submit, /admin/*, /health from Vercel to
  // the Railway backend (see vercel.json rewrites). Browsers only ever talk
  // to spec-ops.best, so the Railway up.railway.app subdomain doesn't have
  // to resolve on the user's device. Empty string = same-origin fetch.
  window.BACKEND_URL = isLocal ? 'http://localhost:8000' : '';
})();
