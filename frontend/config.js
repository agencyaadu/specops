// Deployment config. Edit this file and redeploy the frontend
// to point at a different backend. No build step needed.
(function () {
  var isLocal = location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  // Update the URL below after the Render backend is deployed.
  window.BACKEND_URL = isLocal ? 'http://localhost:8000' : 'https://specops-api-rnq4.onrender.com';
})();
