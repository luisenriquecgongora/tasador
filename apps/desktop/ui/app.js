// The window around the engine: shows its status, picks the port, pins itself on top,
// and shows the engine's own settings page (http://localhost:PORT/) below.
const { invoke } = window.__TAURI__.core;
const { getCurrentWindow } = window.__TAURI__.window;
const $ = (id) => document.getElementById(id);

const ENGINE = {
  starting: ["Engine: starting…", "warn"],
  running: ["Engine: running", "ok"],
  stopped: ["Engine: stopped", ""],
  error: ["Engine: problem", "bad"],
};
const SEARCH = {
  checking: ["Local search: checking…", ""],
  starting: ["Local search: starting in Docker…", "warn"],
  running: ["Local search: running", "ok"],
  unavailable: ["Local search: off (Docker not running)", ""],
  failed: ["Local search: could not start", "bad"],
  configured: ["Search: as chosen in settings", "ok"],
};

let shownUrl = null;
let portEdited = false;

function pill(id, [text, cls]) {
  const node = $(id);
  node.textContent = text;
  node.className = `pill ${cls}`;
}

function note(text, bad = false) {
  $("note").textContent = text;
  $("note").className = bad ? "note bad" : "note";
}

function render(status) {
  pill("engine-pill", ENGINE[status.phase] || [status.phase, ""]);
  pill("search-pill", SEARCH[status.search] || [status.search, ""]);
  if (!portEdited) $("port").value = status.port;

  const running = status.phase === "running";
  $("settings").classList.toggle("hidden", !running);
  $("panel").classList.toggle("hidden", running);
  if (running) {
    if (shownUrl !== status.url) {
      shownUrl = status.url;
      $("settings").src = status.url;
    }
    if (!$("note").classList.contains("bad")) {
      note(`Apps on this computer reach Tasador at ${status.url.replace(/\/$/, "")}. Closing this window keeps it in the tray.`);
    }
  } else {
    shownUrl = null;
    const title = status.phase === "error" ? "Tasador needs your attention" : "Starting…";
    const body = status.message || "Tasador is starting its engine. This takes a few seconds.";
    $("panel").replaceChildren(
      Object.assign(document.createElement("h2"), { textContent: title }),
      Object.assign(document.createElement("pre"), { textContent: body }),
      Object.assign(document.createElement("p"), { className: "note", textContent: `Engine log: ${status.log_file}` }),
    );
  }
}

async function refresh() {
  try {
    render(await invoke("status"));
  } catch (error) {
    note(`Could not read the engine status: ${error}`, true);
  }
}

$("port").addEventListener("input", () => { portEdited = true; });

$("apply").addEventListener("click", async () => {
  const port = Number($("port").value);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    note("Choose a port from 1024 to 65535.", true);
    return;
  }
  $("apply").disabled = true;
  note(`Restarting the engine on port ${port}…`);
  try {
    render(await invoke("set_port", { port }));
    portEdited = false;
    note(`Saved. Tasador now runs on port ${port}.`);
  } catch (error) {
    note(String(error), true);
  } finally {
    $("apply").disabled = false;
  }
});

$("pin").addEventListener("click", async () => {
  const pinned = $("pin").getAttribute("aria-pressed") !== "true";
  await getCurrentWindow().setAlwaysOnTop(pinned);
  $("pin").setAttribute("aria-pressed", String(pinned));
});

$("browser").addEventListener("click", async () => {
  const status = await invoke("status");
  try {
    await invoke("open_in_browser", { url: status.url });
  } catch (error) {
    note(String(error), true);
  }
});

refresh();
setInterval(refresh, 1500);
