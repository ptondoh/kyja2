// Filtres des listes : la liste se met à jour dès qu'on choisit une valeur ou qu'on quitte la zone (Entrée marche aussi).
(function () {
  "use strict";
  document.addEventListener("change", function (e) {
    var input = e.target;
    if (!input.form || !input.form.hasAttribute("data-autofilter")) return;
    if (input.form.requestSubmit) input.form.requestSubmit(); else input.form.submit();
  });
})();
