/*
  Lógica del chat en el navegador.

  1. Al cargar: pide GET /api/models y llena el <select>.
  2. Al enviar: POST /api/chat con el historial completo.
  3. Lee el cuerpo como stream NDJSON: eventos de búsqueda (tarjetas)
     y tokens del modelo.
  4. Guarda cada turno en `history` para que Ollama tenga contexto.
*/

const log = document.getElementById("log");
const form = document.getElementById("form");
const input = document.getElementById("input");
const model = document.getElementById("model");
const go = document.getElementById("go");
const server = document.getElementById("server");
const connectBtn = document.getElementById("connect");
const websearch = document.getElementById("websearch");
const usedb = document.getElementById("usedb");
// Historial {role, content} que se reenvía en cada request (memoria de la sesión).
const history = [];
const charts = [];
const PALETTE = [
  "#c8f06c",
  "#3dffa7",
  "#7dd3fc",
  "#fbbf24",
  "#fb7185",
  "#c4b5fd",
  "#67e8f9",
  "#a3e635",
];
const URL_RE = /https?:\/\/[^\s<>)"']+/g;
const ALLOWED_HOSTS = [
  "laprensa.hn",
  "elheraldo.hn",
  "latribuna.hn",
  "elpais.hn",
  "hondudiario.com",
  "proceso.hn",
  "ellibertador.hn",
  "hch.tv",
  "canal11.hn",
  "facebook.com",
  "fb.com",
  "instagram.com",
  "linkedin.com",
  "lnkd.in",
];

function isAllowedUrl(url) {
  try {
    let host = new URL(url).hostname.toLowerCase();
    if (host.startsWith("www.")) host = host.slice(4);
    return ALLOWED_HOSTS.some((d) => host === d || host.endsWith("." + d));
  } catch {
    return false;
  }
}

websearch.checked = localStorage.getItem("cypher-websearch") === "1";
usedb.checked = localStorage.getItem("cypher-usedb") === "1";
syncPlaceholder();
websearch.addEventListener("change", () => {
  localStorage.setItem("cypher-websearch", websearch.checked ? "1" : "0");
  syncPlaceholder();
});
usedb.addEventListener("change", () => {
  localStorage.setItem("cypher-usedb", usedb.checked ? "1" : "0");
  syncPlaceholder();
});

function syncPlaceholder() {
  if (usedb.checked) {
    input.placeholder = "Pregunta a la base (solo lectura)…";
  } else if (websearch.checked) {
    input.placeholder = "Busca noticias en medios de Honduras…";
  } else {
    input.placeholder = "Escribe un prompt, «busca …» o /sql …";
  }
}

/**
 * Crea una burbuja en #log.
 * @param {"user"|"bot"|"err"} role  estilo y etiqueta ("tú" / "modelo" / "error")
 * @param {string} text              contenido inicial (puede ir vacío y rellenarse)
 * @returns {{hits: HTMLElement|null, copy: HTMLElement}}
 */
function add(role, text) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  el.innerHTML = '<div class="who"></div><div class="body"></div>';
  el.querySelector(".who").textContent =
    role === "user" ? "tú" : role === "err" ? "error" : "modelo";
  const body = el.querySelector(".body");
  if (role === "bot") {
    body.innerHTML = '<div class="hits"></div><div class="copy"></div>';
    const copy = body.querySelector(".copy");
    copy.textContent = text;
    log.appendChild(el);
    log.scrollTop = log.scrollHeight;
    return { hits: body.querySelector(".hits"), copy };
  }
  body.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return { hits: null, copy: body };
}

/** Pinta tarjetas clicables. Solo usa textContent/href para no inyectar HTML. */
function renderHits(container, search) {
  container.replaceChildren();
  const meta = document.createElement("div");
  meta.className = "search-meta";
  meta.textContent = `Prensa de Honduras y redes para “${search.query}”`;
  container.appendChild(meta);
  for (const hit of search.results || []) {
    if (!hit.url || !isAllowedUrl(hit.url)) continue;
    const a = document.createElement("a");
    a.className = "search-hit";
    a.href = hit.url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    const t = document.createElement("div");
    t.className = "t";
    t.textContent = hit.title || hit.url;
    const u = document.createElement("div");
    u.className = "u";
    u.textContent = hit.url;
    a.appendChild(t);
    a.appendChild(u);
    if (hit.snippet) {
      const s = document.createElement("div");
      s.className = "s";
      s.textContent = hit.snippet;
      a.appendChild(s);
    }
    container.appendChild(a);
  }
}

/** Tabla de filas SQL. textContent para no inyectar HTML de la base. */
function renderSql(container, sql) {
  container.replaceChildren();
  const meta = document.createElement("div");
  meta.className = "search-meta";
  meta.textContent = sql.truncated
    ? "Consulta (solo lectura, recortada)"
    : "Consulta (solo lectura)";
  container.appendChild(meta);
  if (sql.sql) {
    const code = document.createElement("pre");
    code.className = "sql-code";
    code.textContent = sql.sql;
    container.appendChild(code);
  }
  if (sql.chart && window.Chart) {
    renderChart(container, sql.chart);
  }
  const cols = sql.columns || [];
  const rows = sql.rows || [];
  if (!cols.length) {
    const empty = document.createElement("div");
    empty.className = "search-meta";
    empty.textContent = "Sin filas";
    container.appendChild(empty);
    return;
  }
  const table = document.createElement("table");
  table.className = "sql-table";
  const thead = document.createElement("thead");
  const trh = document.createElement("tr");
  for (const col of cols) {
    const th = document.createElement("th");
    th.textContent = col;
    trh.appendChild(th);
  }
  thead.appendChild(trh);
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const cell of row) {
      const td = document.createElement("td");
      td.textContent = cell === null || cell === undefined ? "" : String(cell);
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  container.appendChild(table);
}

function hexAlpha(hex, a) {
  const n = hex.replace("#", "");
  const r = parseInt(n.slice(0, 2), 16);
  const g = parseInt(n.slice(2, 4), 16);
  const b = parseInt(n.slice(4, 6), 16);
  return "rgba(" + r + "," + g + "," + b + "," + a + ")";
}

function renderChart(container, spec) {
  const card = document.createElement("div");
  card.className = "chart-card";
  const head = document.createElement("div");
  head.className = "chart-head";
  const title = document.createElement("h3");
  title.textContent = spec.title || "Reporte";
  const dl = document.createElement("button");
  dl.type = "button";
  dl.className = "chart-dl";
  dl.textContent = "PNG";
  head.appendChild(title);
  head.appendChild(dl);
  const wrap = document.createElement("div");
  wrap.className = "chart-wrap";
  const canvas = document.createElement("canvas");
  wrap.appendChild(canvas);
  card.appendChild(head);
  card.appendChild(wrap);
  container.appendChild(card);

  const kind = spec.type || "bar";
  const round = kind === "doughnut" || kind === "pie";
  const datasets = (spec.datasets || []).map((ds, i) => {
    const color = PALETTE[i % PALETTE.length];
    if (round) {
      return {
        label: ds.label,
        data: ds.data,
        backgroundColor: PALETTE.map((c) => hexAlpha(c, 0.85)),
        borderColor: "#0c1110",
        borderWidth: 2,
      };
    }
    return {
      label: ds.label,
      data: ds.data,
      borderColor: color,
      backgroundColor: hexAlpha(color, kind === "line" ? 0.18 : 0.72),
      borderWidth: 2,
      borderRadius: kind === "bar" ? 8 : 0,
      fill: kind === "line",
      tension: 0.35,
      pointRadius: kind === "line" ? 3 : 0,
      pointHoverRadius: 5,
    };
  });
  const chart = new window.Chart(canvas, {
    type: kind,
    data: { labels: spec.labels || [], datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: datasets.length > 1 || round,
          labels: { color: "#e8f0ec", boxWidth: 10, font: { size: 11 } },
        },
        tooltip: {
          backgroundColor: "#171f1c",
          borderColor: "#2a3833",
          borderWidth: 1,
          titleColor: "#c8f06c",
          bodyColor: "#e8f0ec",
        },
      },
      scales: round
        ? {}
        : {
            x: {
              ticks: { color: "#8aa198", maxRotation: 40, font: { size: 10 } },
              grid: { color: "rgba(232,240,236,0.06)" },
            },
            y: {
              ticks: { color: "#8aa198", font: { size: 10 } },
              grid: { color: "rgba(232,240,236,0.08)" },
              beginAtZero: true,
            },
          },
    },
  });
  charts.push(chart);
  dl.onclick = () => {
    const a = document.createElement("a");
    a.href = chart.toBase64Image("image/png", 1);
    a.download = (spec.title || "reporte").replace(/\s+/g, "-").slice(0, 40) + ".png";
    a.click();
  };
}

/** Escribe texto y convierte URLs http(s) en <a> sin interpretar el resto. */
function setTextWithLinks(el, text) {
  el.replaceChildren();
  URL_RE.lastIndex = 0;
  let last = 0;
  let match;
  while ((match = URL_RE.exec(text))) {
    if (match.index > last) {
      el.appendChild(document.createTextNode(text.slice(last, match.index)));
    }
    if (isAllowedUrl(match[0])) {
      const a = document.createElement("a");
      a.href = match[0];
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.textContent = match[0];
      el.appendChild(a);
    } else {
      el.appendChild(document.createTextNode(match[0]));
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) {
    el.appendChild(document.createTextNode(text.slice(last)));
  }
}

/** Rellena el campo servidor con la URL actual (env, CLI o la última pegada). */
async function loadHealth() {
  const r = await fetch("/api/health");
  const data = await r.json();
  if (data.ollama) server.value = data.ollama;
}

/**
 * Guarda el link (local / LAN / ngrok) en el backend y recarga modelos.
 * En la otra PC solo pegas el https de ngrok y pulsas Conectar.
 */
async function connectServer() {
  const url = server.value.trim();
  if (!url) throw new Error("Pega la URL de Ollama o el link de ngrok");
  const r = await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ollama_url: url }),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === "string" ? detail : "No pude guardar la URL");
  }
  server.value = data.ollama || url;
  await loadModels();
}

/** Pide a FastAPI los tags de Ollama y arma las <option> del selector. */
async function loadModels() {
  const r = await fetch("/api/models");
  const data = await r.json();
  model.innerHTML = "";
  const list = data.models.length ? data.models : [data.default];
  for (const name of list) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    if (name === data.default || name.startsWith("qwen3")) opt.selected = true;
    model.appendChild(opt);
  }
}

// Nueva conversación: se pierde el contexto (Ollama no guarda sesiones aquí).
document.getElementById("clear").onclick = () => {
  history.length = 0;
  charts.forEach((c) => c.destroy());
  charts.length = 0;
  log.innerHTML = "";
};

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  add("user", text);
  history.push({ role: "user", content: text });
  const bubble = add("bot", "");
  go.disabled = true;
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model: model.value,
        messages: history,
        stream: true,
        web_search: websearch.checked,
        use_db: usedb.checked,
      }),
    });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let acc = ""; // texto ya mostrado
    let buf = ""; // trozo incompleto entre lecturas (una línea JSON puede partirse)
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop(); // la última puede estar a medias; se espera al siguiente chunk
      for (const line of lines) {
        if (!line.trim()) continue;
        const ev = JSON.parse(line);
        if (ev.error) throw new Error(ev.error);
        if (ev.status === "searching") {
          bubble.copy.textContent = "Buscando: " + ev.query + "…";
          log.scrollTop = log.scrollHeight;
          continue;
        }
        if (ev.status === "querying") {
          bubble.copy.textContent = "Consultando la base: " + ev.query + "…";
          log.scrollTop = log.scrollHeight;
          continue;
        }
        if (ev.search) {
          renderHits(bubble.hits, ev.search);
          acc = "";
          bubble.copy.textContent = "";
          log.scrollTop = log.scrollHeight;
          continue;
        }
        if (ev.sql) {
          renderSql(bubble.hits, ev.sql);
          acc = "";
          bubble.copy.textContent = "";
          log.scrollTop = log.scrollHeight;
          continue;
        }
        acc += ev.message && ev.message.content ? ev.message.content : "";
        setTextWithLinks(bubble.copy, acc);
        log.scrollTop = log.scrollHeight;
      }
    }
    history.push({ role: "assistant", content: acc || "(sin respuesta)" });
    if (!acc) bubble.copy.textContent = "(sin respuesta)";
  } catch (err) {
    add("err", String(err));
  } finally {
    go.disabled = false;
    input.focus();
  }
});

connectBtn.onclick = () => {
  connectServer().catch((e) => add("err", String(e)));
};

server.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    connectBtn.click();
  }
});

loadHealth()
  .then(() => loadModels())
  .catch((e) => add("err", "No pude leer modelos: " + e));
