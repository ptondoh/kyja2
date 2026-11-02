// Cloche des alertes : met à jour le compteur et, si la personne l'a choisi, affiche une alerte du navigateur.
// Interroge le serveur toutes les 30 s tant que la page est visible. Aucun contenu ne passe par innerHTML.
(function () {
  var bell = document.getElementById("bell");
  if (!bell) return;
  var badge = bell.querySelector("[data-bell-count]");
  var KEY = "kyja_last_alert";
  var last = 0;
  try { last = parseInt(localStorage.getItem(KEY) || "0", 10) || 0; } catch (e) {}

  function setCount(n) {
    badge.textContent = n > 99 ? "99+" : String(n);
    badge.hidden = n < 1;
    bell.setAttribute("aria-label", n > 0 ? "Alertes : " + n + " non lue(s)" : "Alertes");
  }

  function show(item) {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    var n = new Notification(item.title, { body: item.body, tag: "kyja-" + item.id });
    n.onclick = function () { window.focus(); if (item.url && item.url.charAt(0) === "/") location.href = item.url; };
  }

  function poll() {
    if (document.hidden) return;
    fetch("/notifications/poll?since=" + last, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        setCount(data.unread);
        if (!data.browser_alert) return;
        data.items.forEach(function (item) {
          if (item.id > last) { last = item.id; show(item); }
        });
        try { localStorage.setItem(KEY, String(last)); } catch (e) {}
      })
      .catch(function () {});
  }

  // Première visite : on mémorise l'état actuel sans réafficher d'anciennes alertes.
  var first = last === 0;
  if (first) {
    fetch("/notifications/poll?since=0", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        setCount(data.unread);
        data.items.forEach(function (item) { if (item.id > last) last = item.id; });
        try { localStorage.setItem(KEY, String(last)); } catch (e) {}
      });
  } else { poll(); }

  setInterval(poll, 30000);
  document.addEventListener("visibilitychange", poll);

  // Demande d'autorisation du navigateur : seulement après un geste de la personne (clic sur la cloche, première fois).
  bell.addEventListener("click", function () {
    if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
  });
  var enable = document.querySelector("[data-enable-browser-alert]");
  if (enable) enable.addEventListener("click", function () {
    if ("Notification" in window) Notification.requestPermission();
  });
})();
