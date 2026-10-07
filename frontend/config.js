/*
 * Firebase web app settings are public identifiers. Never place service-account
 * JSON/private keys, API secrets, or passwords in this file.
 * Firebase values match backend/login_test.html. Local development uses port 8000.
 * Set apiBaseUrl to your deployed backend origin when publishing the frontend.
 */
window.APP_CONFIG = {
  apiBaseUrl: ["localhost", "127.0.0.1"].includes(window.location.hostname)
    ? "http://127.0.0.1:8000" : "",
  firebase: {
    apiKey: "AIzaSyC5wmo_-jm2rtG0dpbCjTbU0fDCWDw7NDU",
    authDomain: "ai-news-card.firebaseapp.com",
    projectId: "ai-news-card",
    appId: "1:450562779610:web:d7ab26e0eb659893a9b8e3",
  },
};
