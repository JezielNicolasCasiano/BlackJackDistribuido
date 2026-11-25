// Comprueba que k6 alcanza la API desde su contenedor.
// Uso: scripts/k6.sh run scripts/k6_conectividad.js
import http from 'k6/http';
import { check } from 'k6';

export const options = { vus: 1, iterations: 5, thresholds: { checks: ['rate==1'] } };

export default function () {
  const r = http.get(`${__ENV.API_URL}/quien`, { timeout: '3s' });
  check(r, { 'la API responde 200': (x) => x.status === 200 });
}
