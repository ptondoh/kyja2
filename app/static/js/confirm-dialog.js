/* Fenêtre « le prix est estimatif » avant la confirmation d'une coiffure du catalogue.
   Amélioration progressive : sans JavaScript (ou sans <dialog>), l'avis reste affiché dans la page
   et le formulaire part normalement. Aucun script en ligne (CSP). */
(() => {
  const form = document.querySelector('form[data-price-dialog]');
  if (!form) return;
  const dialog = document.getElementById(form.dataset.priceDialog);
  if (!dialog || typeof dialog.showModal !== 'function') return;

  const accept = dialog.querySelector('[data-dialog-accept]');
  const cancel = dialog.querySelector('[data-dialog-cancel]');
  let acknowledged = false;

  form.addEventListener('submit', (event) => {
    const submitter = event.submitter;
    // « Modifier » (autre destination) ne demande aucune confirmation.
    if (acknowledged || (submitter && submitter.hasAttribute('formaction'))) return;
    event.preventDefault();
    dialog.showModal();
    accept.focus();
  });

  accept.addEventListener('click', () => {
    acknowledged = true;
    accept.disabled = true; // évite un double envoi
    dialog.close();
    form.requestSubmit();
  });
  cancel.addEventListener('click', () => dialog.close());
})();
