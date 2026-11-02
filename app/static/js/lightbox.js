// Visionneuse (carrousel) pour les photos et vidéos : clic sur une image -> fenêtre plein écran où l'on fait défiler
// avec les flèches du clavier, les boutons ou un glissement du doigt. Rien n'est inséré avec innerHTML.
(function () {
  "use strict";
  if (!window.HTMLDialogElement) return; // très vieux navigateur : les liens ouvrent simplement l'image

  var dialog, stage, counter, btnPrev, btnNext, items = [], index = 0, opener = null;

  function el(tag, cls, attrs) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (attrs) for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function build() {
    dialog = el("dialog", "lightbox", { "aria-label": "Visionneuse de photos et vidéos" });
    stage = el("div", "lightbox-stage");
    counter = el("p", "lightbox-count", { "aria-live": "polite" });
    var close = el("button", "lightbox-btn lightbox-close", { type: "button", "aria-label": "Fermer" });
    close.textContent = "×";
    btnPrev = el("button", "lightbox-btn lightbox-prev", { type: "button", "aria-label": "Précédent" });
    btnPrev.textContent = "‹";
    btnNext = el("button", "lightbox-btn lightbox-next", { type: "button", "aria-label": "Suivant" });
    btnNext.textContent = "›";
    dialog.append(stage, counter, close, btnPrev, btnNext);
    document.body.appendChild(dialog);

    close.addEventListener("click", function () { dialog.close(); });
    btnPrev.addEventListener("click", function () { go(-1); });
    btnNext.addEventListener("click", function () { go(1); });
    dialog.addEventListener("click", function (e) { if (e.target === dialog || e.target === stage) dialog.close(); });
    dialog.addEventListener("close", function () {
      stopMedia();
      stage.textContent = "";
      if (opener && opener.focus) opener.focus();
    });
    dialog.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft") { go(-1); e.preventDefault(); }
      else if (e.key === "ArrowRight") { go(1); e.preventDefault(); }
    });

    // Glissement horizontal sur écran tactile
    var startX = null;
    stage.addEventListener("pointerdown", function (e) { if (e.pointerType === "touch") startX = e.clientX; });
    stage.addEventListener("pointerup", function (e) {
      if (startX === null) return;
      var dx = e.clientX - startX;
      startX = null;
      if (Math.abs(dx) > 50) go(dx < 0 ? 1 : -1);
    });
    stage.addEventListener("pointercancel", function () { startX = null; });
  }

  function stopMedia() {
    var v = stage.querySelector("video");
    if (v) { v.pause(); v.removeAttribute("src"); v.load(); }
  }

  function render() {
    stopMedia();
    stage.textContent = "";
    var it = items[index], node;
    if (it.type === "video") {
      node = el("video", "lightbox-media", { controls: "", playsinline: "", preload: "metadata" });
      if (it.poster) node.setAttribute("poster", it.poster);
      node.setAttribute("src", it.src);
    } else {
      node = el("img", "lightbox-media", { alt: it.alt });
      node.setAttribute("src", it.src);
    }
    stage.appendChild(node);
    counter.textContent = (index + 1) + " / " + items.length + (it.alt ? " · " + it.alt : "");
    var many = items.length > 1;
    btnPrev.hidden = btnNext.hidden = !many;
    if (many) { // précharge la voisine
      var nx = items[(index + 1) % items.length];
      if (nx.type === "image") { var pre = new Image(); pre.src = nx.src; }
    }
  }

  function go(step) {
    if (items.length < 2) return;
    index = (index + step + items.length) % items.length;
    render();
  }

  function collect(group) {
    return Array.prototype.map.call(group.querySelectorAll("[data-lightbox-item]"), function (n) {
      var img = n.querySelector("img");
      return {
        node: n,
        type: n.getAttribute("data-type") === "video" ? "video" : "image",
        src: n.getAttribute("data-src") || n.getAttribute("href"),
        poster: n.getAttribute("data-poster") || "",
        alt: (img && img.getAttribute("alt")) || n.getAttribute("data-alt") || ""
      };
    });
  }

  document.addEventListener("click", function (e) {
    var trigger = e.target.closest && e.target.closest("[data-lightbox-item], [data-lightbox-open]");
    if (!trigger) return;
    var group = trigger.closest("[data-lightbox]");
    if (!group) return;
    var target = trigger.hasAttribute("data-lightbox-item") ? trigger : group.querySelector('[data-lightbox-item][data-for="' + trigger.getAttribute("data-lightbox-open") + '"]');
    if (!target) return;
    e.preventDefault();
    if (!dialog) build();
    items = collect(group);
    index = Math.max(0, items.map(function (i) { return i.node; }).indexOf(target));
    opener = trigger;
    render();
    dialog.showModal();
  });
})();
