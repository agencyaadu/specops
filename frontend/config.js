// Deployment config. Edit this file and redeploy the frontend
// to point at a different backend. No build step needed.
(function () {
  var isLocal = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  // Backend moved from Render (specops-api-rnq4.onrender.com) to Railway
  // because auto-deploy kept stalling on Render. The Railway service is in
  // the agencyaadu workspace, project "specops-api", service "backend".
  window.BACKEND_URL = isLocal ? 'http://localhost:8000' : 'https://backend-production-3d2d.up.railway.app';
})();
