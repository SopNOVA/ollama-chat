/*
  Lógica del chat en el navegador.

  1. Al cargar: pide GET /api/models y llena el <select>.
  2. Al enviar: POST /api/chat con el historial completo.
  3. Lee el cuerpo como stream NDJSON y va concatenando tokens
     en la burbuja del modelo, para que se vea "escribiendo".
  4. Guarda cada turno en `history` para que Ollama tenga contexto.
*/

const log = document.getElementById("log");
const form = document.getElementById("form");
const input = document.getElementById("input");
const model = document.getElementById("model");
const go = document.getElementById("go");
const server = document.getElementById("server");
const connectBtn = document.getElementById("connect");
// Historial {role, content} que se reenvía en cada request (memoria de la sesión).
const history = [];

/**
 * Crea una burbuja en #log.
 * @param {"user"|"bot"|"err"} role  estilo y etiqueta ("tú" / "modelo" / "error")
 * @param {string} text              contenido inicial (puede ir vacío y rellenarse)
 * @returns {HTMLElement}            nodo .body para ir actualizando el stream
 */
function add(role, text) {
  const el = document.createElement("div");
  el.className = "msg " + role;
  el.innerHTML = '<div class="who"></div><div class="body"></div>';
  // textContent (no innerHTML) para no interpretar HTML del modelo.
  el.querySelector(".who").textContent =
    role === "user" ? "tú" : role === "err" ? "error" : "modelo";
  el.querySelector(".body").textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el.querySelector(".body");
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
  const bodyEl = add("bot", "");
  go.disabled = true;
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: model.value, messages: history, stream: true }),
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
        acc += ev.message && ev.message.content ? ev.message.content : "";
        bodyEl.textContent = acc;
        log.scrollTop = log.scrollHeight;
      }
    }
    history.push({ role: "assistant", content: acc || "(sin respuesta)" });
    if (!acc) bodyEl.textContent = "(sin respuesta)";
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
