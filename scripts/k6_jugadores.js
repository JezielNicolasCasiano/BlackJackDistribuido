// Jugadores de blackjack para los experimentos de carga.
// Cada iteración es una partida completa jugada con una estrategia del primer
// proyecto (blackjack/jugador.py), repartidas por usuario virtual (VU).
//
// Uso normal: scripts/jugar.sh, que fija las salidas. A mano:
//   scripts/k6.sh run -e VUS=10 --log-format=raw --console-output=claves.jsonl scripts/k6_jugadores.js
//
// Variables (-e NOMBRE=valor):
//   VUS=10              usuarios virtuales, cada uno juega una partida tras otra
//   DURACION_S=120      tiempo medido
//   CALENTAMIENTO_S=30  tiempo previo que no se cuenta
//   PAUSA_S=0           espera entre una acción y la siguiente ("pensar")
//   CONCURRENTES=0      fracción de partidas que envían dos pedir simultáneos (E3)
//   INTENTOS=3          intentos por acción, con la misma clave
//   ESPERA_S=0.5        espera antes del 1.er reintento; se duplica en cada uno
//   TIMEOUT=8s          espera máxima de k6 por respuesta (la API espera 5 s al procesador)
//   RESUMEN=            archivo JSON para el resumen (lo pone jugar.sh)
//
// Registro de claves: cada acción escribe una línea JSON con console.log. Con
// --console-output esas líneas forman el archivo que verificar.py compara con
// la tabla solicitudes de la base de datos.
import http from 'k6/http';
import exec from 'k6/execution';
import { sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const API = __ENV.API_URL;
const num = (nombre, defecto) => Number(__ENV[nombre] ?? defecto);
const VUS = num('VUS', 10);
const DURACION_S = num('DURACION_S', 120);
const CALENTAMIENTO_S = num('CALENTAMIENTO_S', 30);
const PAUSA_S = num('PAUSA_S', 0);
const CONCURRENTES = num('CONCURRENTES', 0);
const INTENTOS = num('INTENTOS', 3);
const ESPERA_S = num('ESPERA_S', 0.5);
const TIMEOUT = __ENV.TIMEOUT || '8s';
const MAX_ACCIONES = 30; // seguro contra un ciclo infinito por un error del script

// Solo se reintenta lo que se puede arreglar esperando: 503 y 504 son un
// procesador ocupado o caído, y 0 es "sin respuesta" (venció el tiempo de k6 o se
// cortó la conexión). Un 502 (el procesador contestó algo que no es JSON) es una
// respuesta rota que reintentar no arregla, así que no entra; igual que cualquier
// otro no-2xx, termina contada como acción agotada
const REINTENTABLE = new Set([0, 503, 504]);

// Las rutas llevan el id de la partida; sin un nombre fijo, k6 crearía una
// serie de métricas por cada partida
const NOMBRES = {
  crear: 'POST /partidas',
  pedir: 'POST /partidas/{id}/pedir',
  plantarse: 'POST /partidas/{id}/plantarse',
};

const porTipo = (metrica) =>
  Object.fromEntries(Object.keys(NOMBRES).map((op) => [`${metrica}{fase:medicion,op:${op}}`, ['max>=0']]));

export const options = {
  scenarios: {
    jugadores: {
      executor: 'constant-vus',
      vus: VUS,
      duration: `${CALENTAMIENTO_S + DURACION_S}s`,
      gracefulStop: '30s', // deja terminar las partidas en curso
    },
  },
  // Umbrales que siempre se cumplen: sirven para que el resumen incluya las
  // cifras de la fase de medición y de cada tipo de solicitud
  thresholds: {
    'http_req_duration{fase:medicion}': ['max>=0'],
    'http_reqs{fase:medicion}': ['count>=0'],
    'http_req_failed{fase:medicion}': ['rate>=0'],
    ...porTipo('http_req_duration'),
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

// 409 es una respuesta correcta del protocolo (conflicto de versión), no un fallo
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }, 409));

const conflictos = new Counter('conflictos');               // respuestas 409
const reintentos = new Counter('reintentos');               // intentos repetidos con la misma clave
const agotadas = new Counter('acciones_agotadas');          // sin 2xx ni 409 tras todos los intentos
const erroresCliente = new Counter('errores_cliente');      // 404 o 422: error del script
const terminadas = new Counter('partidas_terminadas');
const abandonadas = new Counter('partidas_abandonadas');
const dobles = new Counter('pedir_dobles');
// Segundos desde el inicio del escenario en que se envió cada solicitud. Solo sirve
// para el resumen: su máximo dice hasta cuándo se jugó de verdad (ver handleSummary)
const marcaEnvio = new Trend('marca_envio_s');

// ---- Estrategias del primer proyecto: true si el jugador pide carta ----
const ESTRATEGIAS = {
  conservador: (total) => total < 15,
  arriesgado: (total) => total < 19,
  // Con el crupier mostrando 2-6 es probable que se pase: se juega seguro
  calculador: (total, visible) => (visible <= 6 ? total < 12 : total < 17),
  aleatorio: (total) => (total < 12 ? true : total >= 19 ? false : Math.random() < 0.5),
};
const NOMBRES_ESTRATEGIAS = Object.keys(ESTRATEGIAS);

const desdeInicio = () => (Date.now() - exec.scenario.startTime) / 1000;

// La fase se decide al enviar el primer intento de cada acción y los reintentos la
// conservan: una acción que empieza en la medición y termina de reintentarse en el
// cierre sigue siendo de la medición
function fase() {
  const s = desdeInicio();
  if (s < CALENTAMIENTO_S) return 'calentamiento';
  return s < CALENTAMIENTO_S + DURACION_S ? 'medicion' : 'cierre';
}

function parametros(op, clave, faseAccion) {
  return {
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': clave },
    timeout: TIMEOUT,
    tags: { name: NOMBRES[op], op, fase: faseAccion },
  };
}

function solicitud(op, partida, cuerpo, clave, faseAccion) {
  marcaEnvio.add(desdeInicio());
  const url = op === 'crear' ? `${API}/partidas` : `${API}/partidas/${partida}/${op}`;
  return ['POST', url, JSON.stringify(cuerpo), parametros(op, clave, faseAccion)];
}

// Termina los intentos de una acción cuya primera respuesta ya llegó y deja su
// registro. La clave no cambia entre intentos: eso es lo que permite al
// procesador reconocer el reintento
function completar(op, partida, cuerpo, clave, r, faseAccion) {
  const codigos = [r.status];
  for (let i = 1; i < INTENTOS && REINTENTABLE.has(r.status); i++) {
    reintentos.add(1);
    sleep(ESPERA_S * 2 ** (i - 1));
    const [metodo, url, datos, params] = solicitud(op, partida, cuerpo, clave, faseAccion);
    r = http.request(metodo, url, datos, params);
    codigos.push(r.status);
  }
  const confirmada = r.status >= 200 && r.status < 300;
  if (r.status === 409) conflictos.add(1);
  else if (r.status === 404 || r.status === 422) erroresCliente.add(1);
  else if (!confirmada) agotadas.add(1);

  let estado = null;
  if (confirmada || r.status === 409) {
    try { estado = r.json(); } catch (e) { estado = null; }
  }
  console.log(JSON.stringify({
    t: new Date().toISOString(),
    vu: exec.vu.idInTest,
    fase: faseAccion,
    op,
    clave,
    partida: partida ?? estado?.id ?? null,
    codigos,
    codigo: r.status,
    confirmada,
  }));
  return { codigo: r.status, estado };
}

function enviar(op, partida, cuerpo) {
  const clave = crypto.randomUUID();
  const faseAccion = fase();
  const [metodo, url, datos, params] = solicitud(op, partida, cuerpo, clave, faseAccion);
  return completar(op, partida, cuerpo, clave, http.request(metodo, url, datos, params), faseAccion);
}

// Dos pedir distintos (cada uno con su clave) sobre la misma versión, al mismo
// tiempo. Lo esperado: uno 200 y otro 409
function pedirDoble(p) {
  dobles.add(1);
  const cuerpo = { version: p.version };
  const claves = [crypto.randomUUID(), crypto.randomUUID()];
  const faseAccion = fase();
  const respuestas = http.batch(claves.map((c) => solicitud('pedir', p.id, cuerpo, c, faseAccion)));
  const res = respuestas.map((r, i) => completar('pedir', p.id, cuerpo, claves[i], r, faseAccion));
  // Se sigue con el estado más reciente de los dos
  const utiles = res.filter((x) => x.estado);
  if (utiles.length === 0) return res[0];
  return utiles.reduce((a, b) => (b.estado.version > a.estado.version ? b : a));
}

export default function () {
  const nombre = NOMBRES_ESTRATEGIAS[(exec.vu.idInTest - 1) % NOMBRES_ESTRATEGIAS.length];
  const decidir = ESTRATEGIAS[nombre];

  let r = enviar('crear', null, { jugador: `vu${exec.vu.idInTest}-${nombre}` });
  if (!r.estado) { abandonadas.add(1); return; }
  let p = r.estado;
  let doble = Math.random() < CONCURRENTES;

  for (let n = 0; p.estado === 'en_juego'; n++) {
    if (n >= MAX_ACCIONES) { abandonadas.add(1); return; }
    if (PAUSA_S > 0) sleep(PAUSA_S);
    const pide = decidir(p.jugador.total, p.crupier.total_visible);
    if (pide && doble) {
      doble = false;
      r = pedirDoble(p);
    } else {
      r = enviar(pide ? 'pedir' : 'plantarse', p.id, { version: p.version });
    }
    // 200: sigue jugando. 409: vuelve a decidir con el estado real que trae la
    // respuesta. Cualquier otra cosa (404, 422, intentos agotados): se abandona
    if (!r.estado) { abandonadas.add(1); return; }
    p = r.estado;
  }
  terminadas.add(1);
}

// ---- Resumen al final ----
function valor(data, metrica, campo) {
  const m = data.metrics[metrica];
  return m ? m.values[campo] : undefined;
}

// Duración real de la medición, para calcular solicitudes por segundo. La medición
// empieza en CALENTAMIENTO_S y termina en CALENTAMIENTO_S + DURACION_S, salvo que la
// corrida se haya cortado antes (calor, Ctrl+C): entonces termina en la última
// solicitud enviada. Se toma el menor de los dos finales. En una corrida completa
// la última solicitud cae después del fin nominal y resulta DURACION_S exacto;
// en una cortada, dividir entre DURACION_S subestimaría las solicitudes por segundo
function duracionEfectiva(data) {
  const ultima = valor(data, 'marca_envio_s', 'max');
  if (ultima === undefined) return 0;
  return Math.max(0, Math.min(CALENTAMIENTO_S + DURACION_S, ultima) - CALENTAMIENTO_S);
}

export function handleSummary(data) {
  const med = 'http_req_duration{fase:medicion}';
  const reqs = valor(data, 'http_reqs{fase:medicion}', 'count') ?? 0;
  const efectiva = duracionEfectiva(data);
  const porSegundo = efectiva > 0 ? (reqs / efectiva).toFixed(1) : '-';
  const ms = (x) => (x === undefined ? '-' : `${x.toFixed(1)} ms`);
  const lineas = [
    '',
    `Medición: ${DURACION_S} s con ${VUS} VUs (calentamiento de ${CALENTAMIENTO_S} s fuera)`,
    `  duración efectiva: ${efectiva.toFixed(1)} s` + (efectiva < DURACION_S - 1 ? ' (corrida cortada antes de tiempo)' : ''),
    `  solicitudes: ${reqs} (${porSegundo}/s sobre la duración efectiva)`,
    `  errores: ${((valor(data, 'http_req_failed{fase:medicion}', 'rate') ?? 0) * 100).toFixed(2)} % (409 no cuenta)`,
    `  latencia global: p50 ${ms(valor(data, med, 'med'))}, p95 ${ms(valor(data, med, 'p(95)'))}`,
  ];
  for (const op of Object.keys(NOMBRES)) {
    const m = `http_req_duration{fase:medicion,op:${op}}`;
    lineas.push(`  ${op.padEnd(10)} p50 ${ms(valor(data, m, 'med'))}, p95 ${ms(valor(data, m, 'p(95)'))}`);
  }
  lineas.push('Toda la prueba (calentamiento incluido):');
  const cuenta = (m) => valor(data, m, 'count') ?? 0;
  lineas.push(`  partidas terminadas ${cuenta('partidas_terminadas')}, abandonadas ${cuenta('partidas_abandonadas')}`);
  lineas.push(`  409 ${cuenta('conflictos')}, reintentos ${cuenta('reintentos')}, agotadas ${cuenta('acciones_agotadas')}, ` +
              `errores del script ${cuenta('errores_cliente')}, pedir dobles ${cuenta('pedir_dobles')}`);
  lineas.push('');

  const salida = { stdout: lineas.join('\n') };
  if (__ENV.RESUMEN) {
    const extra = { medicion: { duracion_nominal_s: DURACION_S, duracion_efectiva_s: efectiva } };
    salida[__ENV.RESUMEN] = JSON.stringify({ ...data, ...extra }, null, 2);
  }
  return salida;
}
