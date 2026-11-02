(()=>{const room=document.querySelector('[data-forum-room]');if(!room)return;const seconds=Number(room.dataset.refreshSeconds||0);if(!seconds)return;setInterval(()=>{if(document.hidden)return;const fields=[...document.querySelectorAll('input[name="title"],textarea[name="body"],input[name="body"]')];if(fields.some(field=>field.value.trim()))return;const active=document.activeElement;if(active&&['INPUT','TEXTAREA'].includes(active.tagName))return;location.reload();},seconds*1000);})();

document.addEventListener('submit', event => {
  const form = event.target.closest('form[data-confirm-delete]');
  if (!form) return;
  if (!window.confirm(form.dataset.confirmDelete)) event.preventDefault();
});
