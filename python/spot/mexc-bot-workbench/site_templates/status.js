'use strict';

const badge = document.querySelector('#freshness');
const observed = Date.parse(badge.dataset.observed);

function updateFreshness() {
  const ageMinutes = (Date.now() - observed) / 60000;
  const recent = Number.isFinite(ageMinutes) && ageMinutes >= -5 && ageMinutes <= 30;
  badge.textContent = recent ? 'Recent snapshot' : 'Stale snapshot';
  badge.classList.toggle('recent', recent);
  badge.classList.toggle('stale', !recent);
}

document.querySelector('#refresh').addEventListener('click', () => window.location.reload());
updateFreshness();
setInterval(updateFreshness, 60000);
