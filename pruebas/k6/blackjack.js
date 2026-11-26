// Generador de carga (sección 9). Cada jugador virtual juega partidas completas con una de
// las 4 estrategias del primer proyecto, manda Idempotency-Key y versión, y reintenta
// (3 veces, espera exponencial) ante 503/504/timeout, con la MISMA clave.
//
// Se lanza con scripts/k6.sh (k6 en Docker, que ya define API_URL):
//   scripts/k6.sh run -e VUS=10 -e DURACION=2m pruebas/k6/blackjack.js
// Variables (-e): VUS, DURACION, P_DOBLE (prob. de 2 pedir simultáneos, E3),
//                 PAUSA (segundos entre partidas), TIMEOUT (ej. "5s")
// Cada petición lleva los tags `clave` y `op`; con `--out json=k6.json` queda el registro de
// lo que k6 envió y recibió, que verificar_integridad.py compara contra la BD (sección 7.3).
import http from 'k6/http';
import { sleep } from 'k6';
import { Counter } from 'k6/metrics';

const BASE = __ENV.API_URL || __ENV.BASE_URL || 'http://localhost:8000';
const VUS = parseInt(__ENV.VUS || '10');
const DURACION = __ENV.DURACION || '2m';
const P_DOBLE = parseFloat(__ENV.P_DOBLE || '0');
const PAUSA = parseFloat(__ENV.PAUSA || '0');
const TIMEOUT = __ENV.TIMEOUT || '5s';
const MAX_REINTENTOS = 3;

export const options = {
  scenarios: { carga: { executor: 'constant-vus', vus: VUS, duration: DURACION } },
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
};

// Los 409 son respuestas correctas del protocolo: no cuentan en http_req_failed (sección 9.4)
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }, 409));

const conflictos409 = new Counter('conflictos_409');
const reintentos = new Counter('reintentos');
const agotados = new Counter('reintentos_agotados');
const erroresScript = new Counter('errores_script_404_422');
const partidasTerminadas = new Counter('partidas_terminadas');

// Estrategias del primer proyecto: true = pedir carta
const ESTRATEGIAS = [
  (t, v) => t < 15,                                                    // conservador
  (t, v) => t < 19,                                                    // arriesgado
  (t, v) => (v <= 6 ? t < 12 : t < 17),                                // calculador
  (t, v) => (t < 12 ? true : t >= 19 ? false : Math.random() < 0.5),   // aleatorio
];

function uuid() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function params(clave, op) {
  return {
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': clave },
    timeout: TIMEOUT,
    tags: { clave: clave, op: op },
  };
}

// Envía una acción con reintentos (misma clave). Devuelve la última respuesta.
function enviar(ruta, cuerpo, clave, op) {
  let res;
  for (let intento = 0; intento <= MAX_REINTENTOS; intento++) {
    res = http.post(BASE + ruta, JSON.stringify(cuerpo), params(clave, op));
    const reintentable = res.status === 0 || res.status === 503 || res.status === 504;
    if (!reintentable) return res;
    if (intento < MAX_REINTENTOS) {
      reintentos.add(1);
      sleep(0.2 * Math.pow(2, intento)); // 0.2 s, 0.4 s, 0.8 s
    }
  }
  agotados.add(1);
  return res;
}

function estadoDe(res) {
  try { return res.json(); } catch (e) { return null; }
}

export default function () {
  const claveCrear = uuid();
  // El nombre del jugador sale de la clave: así una partida creada dos veces es detectable
  const rc = enviar('/partidas', { jugador: 'k6-' + claveCrear.slice(0, 8) }, claveCrear, 'crear');
  if (rc.status === 404 || rc.status === 422) { erroresScript.add(1); return; }
  if (rc.status !== 200 && rc.status !== 201) return;
  let estado = estadoDe(rc);
  if (!estado) return;
  const id = estado.id;
  const decidir = ESTRATEGIAS[Math.floor(Math.random() * ESTRATEGIAS.length)];

  // E3: a veces dos "pedir" simultáneos sobre la misma partida con la misma versión
  if (estado.estado === 'en_juego' && Math.random() < P_DOBLE) {
    const reqs = [0, 1].map(() => {
      const k = uuid();
      return ['POST', `${BASE}/partidas/${id}/pedir`,
              JSON.stringify({ version: estado.version }), params(k, 'pedir')];
    });
    const rs = http.batch(reqs);
    rs.forEach((x) => { if (x.status === 409) conflictos409.add(1); });
    const g = http.get(`${BASE}/partidas/${id}`, { timeout: TIMEOUT, tags: { op: 'consultar' } });
    const e2 = estadoDe(g);
    if (!e2) return;
    estado = e2;
  }

  let guardia = 0;
  while (estado.estado === 'en_juego' && guardia++ < 30) {
    const pedir = decidir(estado.jugador.total, estado.crupier.total_visible);
    const op = pedir ? 'pedir' : 'plantarse';
    const clave = uuid(); // clave NUEVA por cada decisión nueva
    const r = enviar(`/partidas/${id}/${op}`, { version: estado.version }, clave, op);
    if (r.status === 200) {
      estado = estadoDe(r);
    } else if (r.status === 409) {
      conflictos409.add(1);
      estado = estadoDe(r); // vuelve a decidir con el estado actual que trae el 409
    } else if (r.status === 404 || r.status === 422) {
      erroresScript.add(1);
      return;
    } else {
      return; // reintentos agotados: se clasifica después comparando con la BD
    }
    if (!estado) return;
  }
  if (estado.estado === 'terminada') partidasTerminadas.add(1);
  if (PAUSA > 0) sleep(PAUSA);
}
